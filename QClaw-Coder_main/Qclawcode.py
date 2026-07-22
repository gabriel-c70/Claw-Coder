
#!/usr/bin/env python3
"""
QClawCode (Q-Claw + Claw-Coder merged).

Q-Claw contributed: the terminal REPL, themes, readline history/completion,
Kokoro TTS, Vosk STT, offline replies, fast-path shell execution, and the
wiki+ddg quick /search.

Claw-Coder contributed: the tool-calling Agent (Ollama tools API), the
Tree-sitter multi-language code RAG, PDF RAG via ChromaDB, safe run_terminal
with confirmation, and the ingest/search-kb pipeline.

CLI:
    python QClawCode.py                    # interactive chat REPL (default)
    python QClawCode.py chat
    python QClawCode.py ingest <path> [--language LANG]
    python QClawCode.py search <query> [--top-k N]
    python QClawCode.py languages
    python QClawCode.py doctor
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import logging
import os
import platform
import random
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import ollama
    import requests
    from ddgs import DDGS
except ImportError as e:
    print(f"Missing core dependencies: {e}. Please run 'python QClawCode.py setup'")
    sys.exit(1)

# Windows readline fallback
try:
    import readline
except ImportError:
    readline = None

# -------------------------------
# LOGGING
# -------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    handlers=[logging.FileHandler("qclawcode.log"), logging.StreamHandler(sys.stderr)],
)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# -------------------------------
# HOME / SETTINGS
# -------------------------------
CLAW_HOME = os.path.expanduser("~/.qclawcode")
_LEGACY_HOMES = [os.path.expanduser("~/Q-Claw")]
os.makedirs(CLAW_HOME, exist_ok=True)

SETTINGS_FILE = os.path.join(CLAW_HOME, "settings.json")
AUTH_FILE = os.path.join(CLAW_HOME, "auth.json")
DEFAULT_SETTINGS = {
    "theme": "light_orange",
    "model": "llama3.2:3b",
    "embedding_model": "qwen3-embedding:4b",
    "db_path": os.path.join(CLAW_HOME, "agent_main_chroma_db"),
    "collection": "agent_knowledge",
    "graph_path": os.path.join(CLAW_HOME, "graph.json"),
    "mic": False,
    "top_k": 4,
    "depth": 2
}

def resource_path(name: str) -> str:
    for base in [CLAW_HOME] + _LEGACY_HOMES:
        candidate = os.path.join(base, name)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(CLAW_HOME, name)

def load_settings() -> Dict[str, Any]:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(SETTINGS, f, indent=2)
    except Exception:
        pass

SETTINGS = load_settings()

# -------------------------------
# HISTORY FILE / READLINE
# -------------------------------
HISTORY_FILE = os.path.join(CLAW_HOME, "history")
if readline:
    readline.set_history_length(1000)
    readline.parse_and_bind("tab: complete")
    try:
        readline.read_history_file(HISTORY_FILE)
    except Exception:
        pass

def save_history():
    if readline:
        try:
            readline.write_history_file(HISTORY_FILE)
        except Exception:
            pass

# -------------------------------
# SLASH COMMANDS (For REPL /help)
# -------------------------------
SLASH_COMMANDS = {
    "/help":    "show this menu",
    "/search":  "wiki + web quick search",
    "/kb":      "search the local RAG knowledge base",
    "/ingest":  "ingest a file (pdf or code) into the knowledge base",
    "/languages": "show tree-sitter code-RAG language support",
    "/doctor":  "check your environment (ollama, RAG deps, voice deps)",
    "/fetch":   "show system info",
    "/mic":     "toggle mic on/off",
    "/listen":  "lock into continuous voice mode (speaks replies, say 'stop' to exit)",
    "/info":    "QClawCode info",
    "/clear":   "clear screen",
    "/reset":   "wipe conversation history (fresh context, screen stays)",
    "/compact": "summarize conversation history into a shorter context",
    "/model":   "show or switch the Ollama model, e.g. /model llama3.2:3b",
    "/audit":   "self-analyze source code for bugs and improvements",
    "/exit":    "quit",
}

def _slash_completer(text, state):
    matches = [c for c in SLASH_COMMANDS if c.startswith(text)]
    matches.sort()
    if state < len(matches):
        return matches[state]
    return None

def _completer(text, state):
    if readline:
        buf = readline.get_line_buffer()
        if buf.startswith("/"):
            return _slash_completer(text, state)
    return None

if readline:
    readline.set_completer(_completer)
    readline.set_completer_delims(" \t\n")

# -------------------------------
# THEMES
# -------------------------------
THEMES = {
    "orange": "\033[1;38;5;214m",
    "light_orange": "\033[1;38;5;220m",
}
RESET = "\033[0m"
ACCENT = THEMES.get(SETTINGS["theme"], THEMES["light_orange"])

# -------------------------------
# VOICE (Kokoro TTS)
# -------------------------------
KOKORO_AVAILABLE = False
_kokoro = None
_kokoro_lock = threading.Lock()
_speaking = False
IN_VOICE_MODE = False

try:
    from kokoro_onnx import Kokoro
    import sounddevice as sd
    import numpy as np
    KOKORO_AVAILABLE = True
except ImportError:
    pass

def _get_kokoro():
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            _kokoro = Kokoro(
                resource_path("kokoro-v1.0.onnx"),
                resource_path("voices-v1.0.bin"),
            )
        return _kokoro

def _preload_kokoro():
    try:
        k = _get_kokoro()
        for phrase in ("hi", "ready", "okay"):
            k.create(phrase, voice="af_heart", speed=0.9, lang="en-us")
    except Exception:
        pass

if KOKORO_AVAILABLE:
    threading.Thread(target=_preload_kokoro, daemon=True).start()

def clean_text(text):
    clean = text
    for code in [ACCENT, RESET]:
        clean = clean.replace(code, "")
    clean = re.sub(r'\033\[[0-9;]*m', '', clean)
    clean = re.sub(r'[•\*#`]', '', clean)
    return clean.strip()

def speak(text):
    if not IN_VOICE_MODE or not KOKORO_AVAILABLE:
        return
    clean = clean_text(text)
    if not clean:
        return

    def _speak():
        global _speaking
        _speaking = True
        try:
            k = _get_kokoro()
            clean_short = clean[:200].rsplit(' ', 1)[0] + "..." if len(clean) > 200 else clean
            samples, sample_rate = k.create(clean_short, voice="af_heart", speed=0.9, lang="en-us")
            sd.play(samples, sample_rate)
        except Exception:
            pass
        finally:
            _speaking = False

    threading.Thread(target=_speak, daemon=True).start()

def wait_speaking():
    while _speaking:
        time.sleep(0.05)

# -------------------------------
# MIC (Vosk STT)
# -------------------------------
def _ensure_pulseaudio():
    try:
        subprocess.run(["pulseaudio", "--check"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        subprocess.Popen(["pulseaudio", "--start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

VOSK_MODEL_PATH = resource_path("vosk-model")
VOSK_AVAILABLE = False
_VOSK_MODEL = None

try:
    import vosk
    import sounddevice as sd
    import queue as queue_mod
    if os.path.isdir(VOSK_MODEL_PATH):
        VOSK_AVAILABLE = True
except ImportError:
    pass

def _suppress_stderr():
    devnull = open(os.devnull, 'w')
    old_fd = os.dup(2)
    os.dup2(devnull.fileno(), 2)
    return old_fd, devnull

def _restore_stderr(old_fd, devnull):
    os.dup2(old_fd, 2)
    os.close(old_fd)
    devnull.close()

def listen_mic():
    global _VOSK_MODEL
    if not VOSK_AVAILABLE:
        qprint("Vosk not available.")
        return None
    _ensure_pulseaudio()
    try:
        import array as array_mod
        if _VOSK_MODEL is None:
            required_dirs = ['am', 'conf', 'graph']
            missing = [d for d in required_dirs if not os.path.isdir(os.path.join(VOSK_MODEL_PATH, d))]
            if missing:
                qprint(f"Vosk model error: Folder exists but is missing: {', '.join(missing)}")
                return None
            old_fd, devnull = _suppress_stderr()
            try:
                _VOSK_MODEL = vosk.Model(VOSK_MODEL_PATH)
            except Exception as e:
                qprint(f"Failed to load Vosk model: {e}")
                return None
            finally:
                _restore_stderr(old_fd, devnull)

        model = _VOSK_MODEL
        q = queue_mod.Queue()
        capture_rate = 48000
        target_rate = 16000
        downsample = capture_rate // target_rate
        blocksize = 8000

        def callback(indata, frames, time_info, status):
            q.put(bytes(indata))

        rec = vosk.KaldiRecognizer(model, target_rate)
        result_text = ""
        silence_count = 0
        max_chunks = 80

        with sd.RawInputStream(samplerate=capture_rate, blocksize=blocksize, dtype="int16", channels=1, callback=callback, device=None):
            for _ in range(max_chunks):
                data = q.get()
                a = array_mod.array('h', data)
                downsampled = bytes(array_mod.array('h', a[::downsample]))
                if rec.AcceptWaveform(downsampled):
                    res = json.loads(rec.Result())
                    text = res.get("text", "").strip()
                    if text:
                        result_text += " " + text
                        silence_count = 0
                    else:
                        silence_count += 1
                        if silence_count >= 4 and result_text.strip():
                            break
                else:
                    partial = json.loads(rec.PartialResult())
                    p = partial.get("partial", "").strip()
                    if p:
                        silence_count = 0
                        sys.stdout.write(f"\r{ACCENT}> {p}...{RESET}    ")
                        sys.stdout.flush()

        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()
        return result_text.strip() if result_text.strip() else None
    except Exception as e:
        qprint(f"Mic error: {e}")
        return None

# -------------------------------
# UI
# -------------------------------
def qprint(t):
    print(ACCENT + t + RESET)

def print_help():
    menu_text = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                          CLAW CODER - Autonomous local AI agent              ║
╚══════════════════════════════════════════════════════════════════════════════╝

📖 USAGE:
  claw <command> [options]

╔══════════════════════════════════════════════════════════════════════════════╗
║ 💬 CHAT & INTERACTION                                                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  chat [--pdf <file>...]         Start interactive chat (optionally preload   ║
║                                PDFs)                                         ║
║  chat --ui textual              Use improved UI with scrolling & selection   ║
║  models                         List local Ollama models                     ║
║  <model-name>                   Start chat with specific Ollama model        ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ 📚 KNOWLEDGE BASE                                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  ingest <paths...>              Ingest files/directories into graph + vector ║
║                                RAG                                           ║
║  ingest-code <file>             Ingest one source file                       ║
║  ingest-pdf <file>              Ingest a PDF or text document (.pdf, .txt,   ║
║                                .md)                                          ║
║  search <query>                 Search vector RAG with graph reranking       ║
║  graph <query>                  Search the knowledge graph only              ║
║  summary                        Show graph node/edge counts                  ║
║  languages                      Show Tree-sitter language support            ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ ⚙  SETUP & CONFIGURATION                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  setup                        Install Python dependencies for Claw Coder     ║
║  doctor                       Check local Node/Python/Ollama setup           ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ 💳 ACCOUNT & BILLING                                                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  login [provider]              Log in via OAuth (default: github)            ║
║  logout                        Clear saved session                           ║
║  whoami                        Show current logged-in user                   ║
║  usage                        Show this month's cloud tool usage             ║
║  credits                      Show paid credit balance                       ║
║  upgrade-plan                 Subscribe to available plans                   ║
║  topup                        Buy extra pay-as-you-go credits                ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ 🔧 COMMON OPTIONS                                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  --top-k <n>                    Number of results to return                  ║
║  --depth <n>                    Graph traversal depth for graph search       ║
║  --graph <file>                 Knowledge graph JSON path                    ║
║  --db <dir>                     ChromaDB directory                           ║
║  --collection <name>            ChromaDB collection                          ║
║  --model <name>                 Ollama chat model                            ║
║  --embedding-model <name>       Ollama embedding model                       ║
║  --ui <rich|textual>            Choose UI style (default: rich)              ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ 📝 EXAMPLES                                                                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  claw setup                    Install dependencies                          ║
║  claw doctor                   Check system setup                            ║
║  claw ingest .                 Ingest current directory                      ║
║  claw graph "imports tree" --depth 2    Search knowledge graph               ║
║  claw search "reranking" --top-k 5       Search with context                 ║
║  claw chat                     Start interactive chat                        ║
║  claw chat --pdf report.pdf    Chat with PDF context                         ║
║  claw chat --ui textual        Use improved UI with scrolling & selection -beta not advised to be used║
║  claw qwen2.5-coder:7b         Use specific model                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """
    print(ACCENT + menu_text + RESET)

def refresh():
    os.system("clear")
    print(ACCENT + r"""
░░▄█▀▀▀░░░░░░░░▀▀▀█▄
▄███▄▄░░▀▄██▄▀░░▄▄███▄
▀██▄▄▄▄████████▄▄▄▄██▀
░░▄▄▄▄██████████▄▄▄▄
░▐▐▀▐▀░▀██████▀░▀▌▀▌▌

    - QClawCoder


""" + RESET)
    t = datetime.now().strftime("%H:%M:%S")
    qprint(f"(Q-Claw + Claw-Coder merged) | {t} | {SETTINGS['model']}")
    print()
    qprint("| /help | claw --help | /clear | /exit")
    print()

def stream(text, delay=0.001):
    sys.stdout.write(ACCENT)
    for c in text:
        sys.stdout.write(c)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write(RESET)
    print()

PONDER = [
    "Thinking...", "Reflecting...", "Considering...", "Analyzing...", "Reasoning...",
    "Processing...", "Working through this...", "Looking at this carefully...",
    "Thinking it through...", "Exploring possibilities...", "Evaluating options...",
    "Connecting ideas...", "Gathering context...", "Reviewing information...",
    "Formulating a response...", "Checking details...", "Piecing this together...",
]

# -------------------------------
# OFFLINE RESPONSES
# -------------------------------
GREETINGS = ["hey", "hi", "hello", "yo", "sup", "what's up", "whats up", "hiya", "howdy"]
GREETING_REPLIES = [
    "Hey. What do you need?", "Hi. Ready when you are.", "Yo. What's up?",
    "Hello. How can I help?", "Hey, what's going on?", "Howdy. What can I do for you?",
    "Hi there. What's on your mind?", "Hey! Good to hear from you.", "Sup. Need something?",
]

HOW_ARE_YOU = [
    "how are you", "how r u", "you ok", "you good", "hows it going", "how's it going",
    "how you doing", "how are things", "whats up", "what's up",
]

HOW_REPLIES = [
    "Running fine. You?", "All systems go.", "Good. What do you need?",
    "Operational. Ask me something.", "Doing well, thanks for asking. What's up?",
    "Pretty good. Ready to help.", "All good here. What about you?",
]

def offline_reply(prompt):
    c = prompt.lower().strip().rstrip("?!.")
    if c in GREETINGS:
        return random.choice(GREETING_REPLIES)
    if c in HOW_ARE_YOU:
        return random.choice(HOW_REPLIES)
    return None

# -------------------------------
# SHELL STATE & FAST PATHS
# -------------------------------
shell_cwd = os.path.expanduser("~")

def shell_exec(cmd):
    global shell_cwd
    stripped = cmd.strip()
    if stripped.startswith("cd ") or stripped == "cd":
        target = stripped[3:].strip() if stripped != "cd" else os.path.expanduser("~")
        target = os.path.expandvars(target)
        if target.startswith("~"):
            target = os.path.expanduser(target)
        new_dir = os.path.normpath(os.path.join(shell_cwd, target))
        if os.path.isdir(new_dir):
            shell_cwd = new_dir
        else:
            print(f"cd: {target}: No such file or directory")
        return
    try:
        result = subprocess.run(cmd, shell=True, cwd=shell_cwd, capture_output=True, text=True, timeout=15)
        if result.stdout: print(result.stdout.rstrip())
        if result.stderr: print(result.stderr.rstrip())
    except subprocess.TimeoutExpired:
        print("(command timed out)")
    except Exception as e:
        print(f"shell error: {e}")

def fetch_info():
    lines = []
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    lines.append(f"  OS       {line.split('=', 1)[1].strip().strip('\"')}")
                    break
    except Exception:
        lines.append("  OS       Unknown")
    lines.append(f"  Model    {SETTINGS['model']}")
    lines.append(f"  Voice    {'available' if KOKORO_AVAILABLE else 'unavailable'}")
    print()
    for l in lines: qprint(l)
    print()

def wiki_search(query):
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
        r = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            if data.get("type") != "disambiguation":
                extract = data.get("extract", "")
                if extract: return extract
    except Exception:
        pass
    return None

def ddg_search(query):
    try:
        r = requests.get("https://api.duckduckgo.com/", params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1, "skip_disambig": 1}, timeout=6)
        data = r.json()
        results = []
        abstract = data.get("AbstractText", "").strip()
        if abstract: results.append(abstract)
        return results if results else None
    except Exception:
        return None

def search(query):
    if not query: return "Search what?"
    wiki = wiki_search(query)
    ddg = ddg_search(query)
    output = f"{query.title()}\n"
    if wiki:
        summary = ". ".join(wiki.split(". ")[:3]).strip()
        output += f"\n{summary}\n"
    if ddg:
        output += "\nWeb Results:\n"
        for item in ddg[:5]: output += f"• {item}\n"
    if not wiki and not ddg: return None
    return output

# ===========================================================================
# RAG / TREE-SITTER / AGENT
# ===========================================================================
LANGUAGE_SPECS = {
    "python": {"module": "tree_sitter_python", "function": "language"},
    "javascript": {"module": "tree_sitter_javascript", "function": "language"},
    "typescript": {"module": "tree_sitter_typescript", "function": "language_typescript"},
    "tsx": {"module": "tree_sitter_typescript", "function": "language_tsx"},
    "json": {"module": "tree_sitter_json", "function": "language"},
    "html": {"module": "tree_sitter_html", "function": "language"},
    "css": {"module": "tree_sitter_css", "function": "language"},
    "c": {"module": "tree_sitter_c", "function": "language"},
    "cpp": {"module": "tree_sitter_cpp", "function": "language"},
    "c_sharp": {"module": "tree_sitter_c_sharp", "function": "language"},
    "java": {"module": "tree_sitter_java", "function": "language"},
    "go": {"module": "tree_sitter_go", "function": "language"},
    "rust": {"module": "tree_sitter_rust", "function": "language"},
}

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}

@dataclass(slots=True)
class Document:
    page_content: str
    metadata: Dict[str, Any]

@dataclass(slots=True)
class RetrievedChunk:
    text: str
    metadata: Dict[str, Any]
    distance: Optional[float]

def require_chromadb():
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError("ChromaDB is missing. Install it with: pip install chromadb") from exc
    return chromadb

def require_pdf_reader():
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is missing. Install it with: pip install pypdf") from exc
    return PdfReader

def require_tree_sitter():
    try:
        from tree_sitter import Language, Parser, Query, QueryCursor
    except ImportError as exc:
        raise RuntimeError("Tree-sitter is missing. Install it with: pip install tree-sitter") from exc
    return Language, Parser, Query, QueryCursor

def available_languages() -> Dict[str, Dict[str, Any]]:
    status = {}
    for language, spec in LANGUAGE_SPECS.items():
        try:
            module = importlib.import_module(spec["module"])
            getattr(module, spec["function"])
            status[language] = {"available": True, "module": spec["module"], "install": None}
        except Exception:
            package = spec["module"].replace("_", "-")
            status[language] = {"available": False, "module": spec["module"], "install": f"pip install {package}"}
    return status

def infer_language(path: str) -> Optional[str]:
    return EXTENSION_TO_LANGUAGE.get(Path(path).suffix.lower())

def load_tree_sitter_language(language_name: str):
    Language, Parser, Query, QueryCursor = require_tree_sitter()
    spec = LANGUAGE_SPECS.get(language_name)
    if not spec:
        raise RuntimeError(f"Unsupported language '{language_name}'.")
    try:
        module = importlib.import_module(spec["module"])
        language_fn = getattr(module, spec["function"])
    except Exception as exc:
        raise RuntimeError(f"Tree-sitter grammar for {language_name} is missing. Install it with: pip install {spec['module'].replace('_', '-')}") from exc
    parser = Parser()
    parser.language = Language(language_fn())
    return parser, Query, QueryCursor, parser.language

def node_text(source: bytes, node: Any) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

def load_pdf(path: str) -> List[Document]:
    pdf_path = Path(path).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    PdfReader = require_pdf_reader()
    pdf_reader = PdfReader(str(pdf_path))
    docs = []
    for index, page in enumerate(pdf_reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            docs.append(Document(page_content=text, metadata={"source": str(pdf_path), "page": index, "kind": "pdf"}))
    return docs

def load_text_file(path: str) -> List[Document]:
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Text file not found: {file_path}")
    text = file_path.read_text(encoding="utf-8", errors="replace")
    return [Document(page_content=text, metadata={"source": str(file_path), "page": 1, "kind": "text"})]

def split_documents(documents: Iterable[Document], chunk_size: int = 1200, chunk_overlap: int = 250) -> List[Document]:
    if chunk_size <= chunk_overlap: raise ValueError("chunk_size must be greater than chunk_overlap")
    chunks = []
    step = chunk_size - chunk_overlap
    for doc in documents:
        text = " ".join(doc.page_content.split())
        for start in range(0, len(text), step):
            end = min(start + chunk_size, len(text))
            chunk_text = text[start:end].strip()
            if chunk_text:
                metadata = dict(doc.metadata)
                metadata.update({"chunk_start": start, "chunk_end": end})
                chunks.append(Document(page_content=chunk_text, metadata=metadata))
            if end >= len(text): break
    return chunks

def fallback_code_chunks(path: str, text: str, language: str, chunk_size: int = 1200) -> List[Document]:
    chunks = []
    for index, start in enumerate(range(0, len(text), chunk_size)):
        end = min(start + chunk_size, len(text))
        content = text[start:end].strip()
        if content:
            chunks.append(Document(page_content=content, metadata={
                "source": str(Path(path).resolve()), "kind": "code", "language": language,
                "symbol_type": "text_chunk", "symbol_name": f"chunk_{index}", "start_byte": start, "end_byte": end
            }))
    return chunks

def query_for_language(language: str) -> Optional[str]:
    if language == "python":
        return """
        (function_definition name: (identifier) @name) @definition.function
        (class_definition name: (identifier) @name) @definition.class
        """
    if language == "javascript":
        return """
        (function_declaration name: (identifier) @name) @definition.function
        (class_declaration name: (identifier) @name) @definition.class
        (method_definition name: (property_identifier) @name) @definition.method
        """
    if language in {"typescript", "tsx"}:
        return """
        (function_declaration name: (identifier) @name) @definition.function
        (class_declaration name: (type_identifier) @name) @definition.class
        (method_definition name: (property_identifier) @name) @definition.method
        """
    if language == "java":
        return """
        (class_declaration name: (identifier) @name) @definition.class
        (method_declaration name: (identifier) @name) @definition.method
        """
    if language == "go":
        return """
        (function_declaration name: (identifier) @name) @definition.function
        (method_declaration name: (field_identifier) @name) @definition.method
        (type_declaration (type_spec name: (type_identifier) @name)) @definition.type
        """
    if language == "rust":
        return """
        (function_item name: (identifier) @name) @definition.function
        (struct_item name: (type_identifier) @name) @definition.struct
        (enum_item name: (type_identifier) @name) @definition.enum
        (impl_item) @definition.impl
        """
    if language == "c":
        return """
        (function_definition declarator: (function_declarator declarator: (identifier) @name)) @definition.function
        """
    if language == "cpp":
        return """
        (function_definition declarator: (function_declarator declarator: (identifier) @name)) @definition.function
        (class_specifier name: (type_identifier) @name) @definition.class
        """
    if language == "c_sharp":
        return """
        (class_declaration name: (identifier) @name) @definition.class
        (method_declaration name: (identifier) @name) @definition.method
        """
    return None

def tree_sitter_code_chunks(path: str, language: Optional[str] = None) -> List[Document]:
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Code file not found: {file_path}")
    detected_language = language or infer_language(str(file_path))
    if not detected_language:
        raise RuntimeError(f"Could not infer language for file: {file_path}")
    text = file_path.read_text(encoding="utf-8", errors="replace")
    source = text.encode("utf-8")
    parser, Query, QueryCursor, ts_language = load_tree_sitter_language(detected_language)
    tree = parser.parse(source)
    query_text = query_for_language(detected_language)
    if not query_text:
        return fallback_code_chunks(str(file_path), text, detected_language)
    captures = QueryCursor(Query(ts_language, query_text)).captures(tree.root_node)
    name_by_position = {}
    for name_node in captures.get("name", []):
        name_by_position[(name_node.start_byte, name_node.end_byte)] = node_text(source, name_node)
    chunks = []
    for capture_name, nodes in captures.items():
        if capture_name == "name": continue
        for node in nodes:
            symbol_name = "anonymous"
            for child in node.children:
                key = (child.start_byte, child.end_byte)
                if key in name_by_position:
                    symbol_name = name_by_position[key]
                    break
            content = node_text(source, node).strip()
            if not content: continue
            chunks.append(Document(page_content=content, metadata={
                "source": str(file_path), "kind": "code", "language": detected_language,
                "symbol_type": capture_name.replace("definition.", ""), "symbol_name": symbol_name,
                "start_byte": node.start_byte, "end_byte": node.end_byte
            }))
    return chunks or fallback_code_chunks(str(file_path), text, detected_language)

def stable_id(document: Document) -> str:
    source = document.metadata.get("source", "unknown")
    kind = document.metadata.get("kind", "unknown")
    start = document.metadata.get("chunk_start", document.metadata.get("start_byte", 0))
    name = document.metadata.get("symbol_name", "")
    digest = hashlib.sha256(f"{source}:{kind}:{start}:{name}:{document.page_content}".encode("utf-8")).hexdigest()
    return digest[:24]

def ollama_embed(texts: Iterable[str], model: str) -> List[List[float]]:
    values = list(texts)
    if not values: return []
    try:
        response = ollama.embed(model=model, input=values)
    except Exception as exc:
        raise RuntimeError("Ollama embedding failed. Make sure Ollama is running and the embedding model is pulled.") from exc
    embeddings = response.get("embeddings")
    if embeddings: return embeddings
    raise RuntimeError("Ollama did not return embeddings.")

# -------------------------------
# KNOWLEDGE GRAPH
# -------------------------------
class KnowledgeGraph:
    def __init__(self, path: str):
        self.path = path
        self.nodes = []
        self.edges = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as f:
                    data = json.load(f)
                    self.nodes = data.get("nodes", [])
                    self.edges = data.get("edges", [])
            except Exception:
                pass

    def save(self):
        try:
            with open(self.path, 'w') as f:
                json.dump({"nodes": self.nodes, "edges": self.edges}, f, indent=2)
        except Exception:
            pass

    def add_node(self, node_id: str, node_type: str, name: str, source: str):
        if not any(n.get("id") == node_id for n in self.nodes):
            self.nodes.append({"id": node_id, "type": node_type, "name": name, "source": source})

    def add_edge(self, source_id: str, target_id: str, relation: str):
        if not any(e.get("source") == source_id and e.get("target") == target_id and e.get("relation") == relation for e in self.edges):
            self.edges.append({"source": source_id, "target": target_id, "relation": relation})

    def summary(self):
        return len(self.nodes), len(self.edges)

    def search(self, query: str, depth: int = 2):
        results = []
        for node in self.nodes:
            if query.lower() in node.get("name", "").lower():
                results.append(node)
        return results

class MixedRAGStore:
    def __init__(self, db_path: str, collection_name: str, embedding_model: str) -> None:
        chromadb = require_chromadb()
        self.embedding_model = embedding_model
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def add_documents(self, documents: List[Document]) -> int:
        if not documents: return 0
        ids = [stable_id(document) for document in documents]
        texts = [document.page_content for document in documents]
        metadatas = [document.metadata for document in documents]
        embeddings = ollama_embed(texts, model=self.embedding_model)
        self.collection.upsert(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)
        return len(documents)

    def ingest_pdf(self, path: str, chunk_size: int = 1200, chunk_overlap: int = 250) -> int:
        pages = load_pdf(path)
        chunks = split_documents(pages, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return self.add_documents(chunks)

    def ingest_text(self, path: str, chunk_size: int = 1200, chunk_overlap: int = 250) -> int:
        pages = load_text_file(path)
        chunks = split_documents(pages, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return self.add_documents(chunks)

    def ingest_code(self, path: str, language: Optional[str] = None) -> int:
        chunks = tree_sitter_code_chunks(path, language=language)
        return self.add_documents(chunks)

    def search(self, query: str, top_k: int = 4) -> List[RetrievedChunk]:
        if not query.strip(): raise ValueError("query cannot be empty")
        query_embedding = ollama_embed([query], model=self.embedding_model)[0]
        result = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=max(1, top_k),
            include=["documents", "metadatas", "distances"],
        )
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            RetrievedChunk(text=text, metadata=metadata or {}, distance=float(distance) if distance is not None else None)
            for text, metadata, distance in zip(documents, metadatas, distances)
        ]

class Agent:
    def __init__(self, model: str, embedding_model: str, db_path: str, collection: str, max_steps: int = 8) -> None:
        self.model = model
        self.embedding_model = embedding_model
        self.db_path = db_path
        self.collection = collection
        self.max_steps = max_steps
        self._rag_store = None
        self.messages = [{"role": "system", "content": self.build_system_prompt()}]
        self.tools = []
        self.setup_tools()

    @staticmethod
    def build_system_prompt() -> str:
        return (
            "You are QClawCode, a sharp, capable local AI assistant that merges a terminal "
            "chat persona with RAG search, code/PDF ingestion, web search, and terminal tools.\n"
            "Be concise, direct, and useful. Answer immediately. Avoid filler, repetition, "
            "apologies, and unnecessary enthusiasm. Never begin responses with 'Sure', "
            "'Of course', 'Certainly', 'Absolutely', 'Great question', or similar.\n\n"
            "Use search_knowledge_base for questions about ingested PDFs or local code.\n"
            "Use ingest_pdf_knowledge or ingest_code_knowledge only when asked to ingest files.\n"
            "Use search_stuff for outside web facts or current information.\n"
            "Use run_terminal only for local commands, tests, and file inspection.\n"
            "Use retrieved context directly. If context is insufficient, say what is missing."
        )

    def reset(self):
        self.messages = [self.messages[0]]

    def rag_store(self) -> MixedRAGStore:
        if self._rag_store is None:
            self._rag_store = MixedRAGStore(
                db_path=self.db_path, collection_name=self.collection, embedding_model=self.embedding_model
            )
        return self._rag_store

    def setup_tools(self) -> None:
        self.tools = [
            {"type": "function", "function": {
                "name": "search_knowledge_base",
                "description": "Search ingested PDFs and source code using ChromaDB RAG.",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}, "top_k": {"type": "integer", "default": 4}},
                    "required": ["query"]},
            }},
            {"type": "function", "function": {
                "name": "ingest_code_knowledge",
                "description": "Ingest a local source-code file into the Tree-sitter RAG database.",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"}, "language": {"type": "string"}},
                    "required": ["path"]},
            }},
            {"type": "function", "function": {
                "name": "ingest_pdf_knowledge",
                "description": "Ingest a local PDF or text file (.pdf, .txt, .md) into the RAG database.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            }},
            {"type": "function", "function": {
                "name": "search_stuff",
                "description": "Search the internet for current or outside information.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            }},
            {"type": "function", "function": {
                "name": "open_default_browser",
                "description": "Open a URL in the default browser.",
                "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            }},
            {"type": "function", "function": {
                "name": "run_terminal",
                "description": "Run a local terminal command.",
                "parameters": {"type": "object", "properties": {
                    "command": {"type": "string"}, "timeout": {"type": "integer", "default": 30}},
                    "required": ["command"]},
            }},
        ]

    @staticmethod
    def parse_tool_arguments(raw_args: Any) -> Dict[str, Any]:
        if isinstance(raw_args, dict): return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                return {"value": raw_args}
        return {}

    def execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        try:
            if tool_name == "search_knowledge_base": return self._search_knowledge_base_tool(tool_input)
            if tool_name == "ingest_code_knowledge": return self._ingest_code_tool(tool_input)
            if tool_name == "ingest_pdf_knowledge": return self._ingest_pdf_tool(tool_input)
            if tool_name == "search_stuff": return self._search_tool(tool_input)
            if tool_name == "open_default_browser": return self._open_browser_tool(tool_input)
            if tool_name == "run_terminal": return self._run_terminal_tool(tool_input)
            return json.dumps({"status": "error", "error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            logging.error("Tool failed: %s", exc)
            return json.dumps({"status": "error", "tool": tool_name, "error": str(exc)}, ensure_ascii=False)

    def _search_knowledge_base_tool(self, tool_input: Dict[str, Any]) -> str:
        query = str(tool_input.get("query", "")).strip()
        top_k = int(tool_input.get("top_k", 4))
        if not query: return json.dumps({"status": "error", "error": "Missing query"})
        chunks = self.rag_store().search(query=query, top_k=top_k)
        return json.dumps({
            "status": "ok", "query": query,
            "chunks": [{"text": c.text, "metadata": c.metadata, "distance": c.distance} for c in chunks],
        }, ensure_ascii=False)

    def _ingest_code_tool(self, tool_input: Dict[str, Any]) -> str:
        path = str(tool_input.get("path", "")).strip()
        language = tool_input.get("language")
        if not path: return json.dumps({"status": "error", "error": "Missing path"})
        chunks = tree_sitter_code_chunks(path, language=language)
        count = self.rag_store().add_documents(chunks)
        return json.dumps({"status": "ok", "path": path, "chunks_added": count}, ensure_ascii=False)

    def _ingest_pdf_tool(self, tool_input: Dict[str, Any]) -> str:
        path = str(tool_input.get("path", "")).strip()
        if not path: return json.dumps({"status": "error", "error": "Missing path"})
        if path.endswith((".txt", ".md")):
            count = self.rag_store().ingest_text(path)
        else:
            count = self.rag_store().ingest_pdf(path)
        return json.dumps({"status": "ok", "path": path, "chunks_added": count}, ensure_ascii=False)

    def search_info(self, query: str, max_results: int = 5) -> Optional[str]:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
                if not results: return "No search results"
                formatted = []
                for index, result in enumerate(results, start=1):
                    formatted.append(
                        f"Result {index}\nTitle: {result.get('title', 'No title')}\n"
                        f"Snippet: {result.get('body', 'No body')}\nSource: {result.get('href', '')}"
                    )
                return "\n\n".join(formatted)
        except Exception as exc:
            logging.error("Search failed: %s", exc)
            return None

    def _search_tool(self, tool_input: Dict[str, Any]) -> str:
        query = str(tool_input.get("query", "")).strip()
        if not query: return json.dumps({"status": "error", "error": "Missing query"})
        result = self.search_info(query)
        if result is None: return json.dumps({"status": "error", "query": query, "error": "Search failed."})
        return json.dumps({"status": "ok", "query": query, "results": result}, ensure_ascii=False)

    def _open_browser_tool(self, tool_input: Dict[str, Any]) -> str:
        url = str(tool_input.get("url", "")).strip()
        if not url: return json.dumps({"status": "error", "error": "Missing url"})
        if not urlparse(url).scheme: url = f"https://{url}"
        try:
            opened = bool(webbrowser.open(url, new=2))
        except Exception as exc:
            return json.dumps({"status": "error", "url": url, "error": str(exc)})
        if not opened: return json.dumps({"status": "error", "url": url, "error": "Browser could not open"})
        return json.dumps({"status": "ok", "url": url})

    @staticmethod
    def is_read_only_command(command: str) -> bool:
        try:
            parts = shlex.split(command)
        except ValueError:
            return False
        if not parts: return True
        if any(symbol in command for symbol in [">", ">>", "2>", "| tee", "&&", "||"]): return False
        if parts[0] in {"ls", "pwd", "whoami", "cat", "head", "tail", "grep", "find", "date", "echo", "wc"}: return True
        if parts[0] == "git" and len(parts) > 1: return parts[1] in {"status", "log", "show", "diff", "branch", "remote", "rev-parse"}
        if parts[0] in {"python", "python3"}: return any(flag in parts for flag in ("--version", "-V"))
        return False

    def needs_confirmation(self, command: str) -> bool:
        lowered = f" {command.strip().lower()} "
        high_risk = [
            "sudo ", " rm ", "rm -", "mv ", "cp ", "chmod ", "chown ",
            "git commit", "git push", "git reset", "git clean",
            "pip install", "pip uninstall",
        ]
        return any(marker in lowered for marker in high_risk) or not self.is_read_only_command(command)

    def ask_user_confirmation(self, command: str) -> bool:
        print("\nTool requested this terminal command:")
        print(f"  {command}")
        answer = input("Run this command? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    @staticmethod
    def decode_process_output(value: Any) -> str:
        if value is None: return ""
        if isinstance(value, bytes): return value.decode("utf-8", errors="replace")
        return str(value)

    def run_terminal(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=max(1, timeout))
            return {"command": command, "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.TimeoutExpired as exc:
            return {
                "command": command, "stdout": self.decode_process_output(exc.stdout),
                "stderr": f"Command timed out after {timeout} seconds.", "returncode": 124,
            }

    def _run_terminal_tool(self, tool_input: Dict[str, Any]) -> str:
        command = str(tool_input.get("command", "")).strip()
        if not command: return json.dumps({"status": "error", "error": "Missing command"})
        timeout = int(tool_input.get("timeout", 30))
        if self.needs_confirmation(command) and not self.ask_user_confirmation(command):
            return json.dumps({"status": "cancelled", "command": command})
        result = self.run_terminal(command, timeout=timeout)
        status = "ok" if result["returncode"] == 0 else "error"
        return json.dumps({"status": status, "result": result}, ensure_ascii=False)

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        use_tools = True
        for _ in range(self.max_steps):
            try:
                if use_tools:
                    response = ollama.chat(model=self.model, messages=self.messages, tools=self.tools, stream=False)
                else:
                    response = ollama.chat(model=self.model, messages=self.messages, stream=False)
            except Exception as e:
                if "does not support tools" in str(e).lower() and use_tools:
                    use_tools = False
                    continue
                raise e

            message = response.get("message", {})
            assistant_message = {"role": "assistant", "content": message.get("content", "")}
            tool_calls = message.get("tool_calls") if use_tools else None

            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            self.messages.append(assistant_message)

            if not tool_calls:
                return message.get("content", "")

            for call in tool_calls:
                function_data = call.get("function", {})
                tool_name = function_data.get("name", "")
                tool_args = self.parse_tool_arguments(function_data.get("arguments", {}))
                result = self.execute_tool(tool_name, tool_args)
                self.messages.append({"role": "tool", "content": result})

        return "I reached the tool-execution step limit before finishing."

# Instantiate the agent globally using settings
agent = Agent(
    model=SETTINGS["model"],
    embedding_model=SETTINGS["embedding_model"],
    db_path=SETTINGS["db_path"],
    collection=SETTINGS["collection"],
)

# -------------------------------
# CLI COMMAND IMPLEMENTATIONS
# -------------------------------
def show_languages():
    status = available_languages()
    qprint("Tree-sitter language support:")
    for lang, info in status.items():
        if info["available"]:
            qprint(f"  ✓ {lang}")
        else:
            qprint(f"  ✗ {lang}  (install: {info['install']})")

def run_doctor():
    qprint("Checking environment setup...")
    try:
        node_v = subprocess.check_output(["node", "--version"], text=True).strip()
        qprint(f"  Node:       ✓ ({node_v})")
    except Exception:
        qprint("  Node:       ✗ (not found)")
    qprint(f"  Python:     ✓ ({sys.version.split()[0]})")
    try:
        models = ollama.list()
        qprint(f"  Ollama:     ✓ ({len(models.get('models', []))} models)")
    except Exception:
        qprint("  Ollama:     ✗ (not running)")
    try:
        require_chromadb()
        qprint("  ChromaDB:   ✓")
    except Exception:
        qprint("  ChromaDB:   ✗")
    try:
        require_tree_sitter()
        qprint("  Tree-sitter: ✓")
    except Exception:
        qprint("  Tree-sitter: ✗")
    try:
        require_pdf_reader()
        qprint("  pypdf:      ✓")
    except Exception:
        qprint("  pypdf:      ✗")
    qprint(f"  Kokoro TTS: {'✓' if KOKORO_AVAILABLE else '✗'}")
    qprint(f"  Vosk STT:   {'✓' if VOSK_AVAILABLE else '✗'}")

def run_setup():
    qprint("Running setup: installing Python dependencies for Claw Coder...")
    deps = ["ollama", "requests", "chromadb", "pypdf", "tree-sitter", "tree-sitter-python", "tree-sitter-javascript", "tree-sitter-typescript", "ddgs"]
    try:
        subprocess.run([sys.executable, "-m", "pip", "install"] + deps, check=True)
        qprint("Setup complete.")
    except subprocess.CalledProcessError as e:
        qprint(f"Setup failed: {e}")

def handle_account(cmd, args):
    if cmd == "login":
        provider = args[0] if args else "github"
        qprint(f"Logging in via {provider} OAuth (mocked)...")
        session = {"user": "local_dev", "provider": provider, "credits": 100, "plan": "free"}
        with open(AUTH_FILE, "w") as f:
            json.dump(session, f)
        qprint("Logged in as local_dev.")
    elif cmd == "logout":
        if os.path.exists(AUTH_FILE):
            os.remove(AUTH_FILE)
        qprint("Logged out.")
    elif cmd == "whoami":
        try:
            with open(AUTH_FILE) as f:
                session = json.load(f)
                qprint(f"User: {session.get('user')} (Plan: {session.get('plan')})")
        except FileNotFoundError:
            qprint("Not logged in.")
    elif cmd == "usage":
        qprint("This month's usage: 0/10000 tokens (mocked).")
    elif cmd == "credits":
        qprint("Paid credit balance: 100 credits (mocked).")
    elif cmd == "upgrade-plan":
        qprint("Available plans: Free, Pro ($20/mo), Enterprise. (Mocked checkout)")
    elif cmd == "topup":
        qprint("Buy extra credits: 10$ for 1000 credits. (Mocked checkout)")

def ingest_file(path, force_code=False, force_text=False):
    if not os.path.exists(path):
        qprint(f"File not found: {path}")
        return
    try:
        if path.endswith(".pdf"):
            count = agent.rag_store().ingest_pdf(path)
            qprint(f"Ingested PDF {path}: {count} chunks")
        elif path.endswith((".txt", ".md")) or force_text:
            count = agent.rag_store().ingest_text(path)
            qprint(f"Ingested Text {path}: {count} chunks")
        else:
            lang = infer_language(path)
            if not lang and not force_code:
                qprint(f"Skipping {path} (unsupported language)")
                return
            count = agent.rag_store().ingest_code(path, language=lang)
            qprint(f"Ingested Code {path}: {count} chunks")

            # Add to Knowledge Graph
            graph = KnowledgeGraph(SETTINGS.get("graph_path", os.path.join(CLAW_HOME, "graph.json")))
            graph.add_node(str(path), "file", os.path.basename(path), str(path))
            if lang:
                for chunk in tree_sitter_code_chunks(path, language=lang):
                    if chunk.metadata.get("symbol_name") and chunk.metadata.get("symbol_name") != "anonymous":
                        graph.add_node(
                            f"{path}:{chunk.metadata['symbol_name']}",
                            chunk.metadata.get("symbol_type", "symbol"),
                            chunk.metadata["symbol_name"],
                            str(path)
                        )
            graph.save()
    except Exception as e:
        qprint(f"Failed to ingest {path}: {e}")

def start_textual_ui():
    try:
        from textual.app import App
        qprint("Textual UI selected. Starting basic Textual interface (beta)...")
        qprint("Textual UI not fully configured. Falling back to standard REPL.")
        chat_repl()
    except ImportError:
        qprint("Textual is not installed. Falling back to standard REPL.")
        chat_repl()

# -------------------------------
# REPL & AGENT INVOCATION
# -------------------------------
def ask_agent(prompt):
    t0 = time.time()
    msg = random.choice(PONDER)
    sys.stdout.write(ACCENT + msg + "..." + RESET)
    sys.stdout.flush()

    try:
        off_reply = offline_reply(prompt)
        if off_reply:
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()
            stream(off_reply)
            return

        reply = agent.chat(prompt)
    except Exception as e:
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()

        err_str = str(e).lower()
        if "not found" in err_str or "404" in err_str:
            qprint(f"Agent error: Model '{SETTINGS['model']}' not found in Ollama.")
            qprint(f"-> Fix: Run 'ollama pull {SETTINGS['model']}' in your terminal to download it.")
            qprint(f"-> Or switch to an installed model using /model <name>")
        else:
            qprint(f"Agent error: {type(e).__name__}: {e}")
        return

    elapsed = time.time() - t0
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()
    stream(reply)
    qprint(f"({elapsed:.1f}s)")

    if IN_VOICE_MODE:
        speak(reply)
        wait_speaking()

def toggle_mic():
    SETTINGS["mic"] = not SETTINGS.get("mic", False)
    save_settings()
    qprint(f"Mic {'ON' if SETTINGS['mic'] else 'OFF'}")

def toggle_voice_mode():
    global IN_VOICE_MODE
    if not KOKORO_AVAILABLE or not VOSK_AVAILABLE:
        qprint("Voice mode requires Kokoro and Vosk.")
        return
    IN_VOICE_MODE = not IN_VOICE_MODE
    qprint(f"Voice mode {'ON' if IN_VOICE_MODE else 'OFF'}")

def handle_slash_command(user_input):
    # Strip 'claw ' if the user accidentally typed it inside the REPL
    if user_input.lower().startswith("claw "):
        user_input = user_input[5:]

    # Allow CLI commands to work inside REPL even without a slash
    if not user_input.startswith("/"):
        cmd_check = user_input.split()[0].lower() if user_input.split() else ""

        if cmd_check in ["help", "--help", "-h", "setup", "doctor", "models", "languages", "summary",
                         "ingest", "ingest-code", "ingest-pdf", "search", "graph",
                         "login", "logout", "whoami", "usage", "credits", "upgrade-plan", "topup"]:
            main(args=user_input.split())
            return True
        return False

    parts = user_input.split()
    cmd = parts[0]
    args = parts[1:]

    if cmd == "/exit":
        raise EOFError
    elif cmd == "/clear":
        os.system("clear")
    elif cmd == "/help":
        # Keep the REPL help simple and separate from the CLI claw --help
        qprint("Q-Claw:")
        for k, v in SLASH_COMMANDS.items():
            qprint(f"  {k:12} - {v}")
    elif cmd == "/search":
        query = " ".join(args)
        res = search(query)
        if res: print(res)
        else: qprint("No results found.")
    elif cmd == "/kb":
        query = " ".join(args)
        chunks = agent.rag_store().search(query, top_k=SETTINGS.get("top_k", 4))
        for i, c in enumerate(chunks, 1):
            qprint(f"[{i}] {c.metadata.get('source')} (dist: {c.distance:.2f})")
            print(c.text[:200] + "...\n")
    elif cmd == "/ingest":
        if not args:
            qprint("Usage: /ingest <file>")
        else:
            ingest_file(" ".join(args))
    elif cmd == "/languages":
        show_languages()
    elif cmd == "/doctor":
        run_doctor()
    elif cmd == "/fetch":
        fetch_info()
    elif cmd == "/mic":
        toggle_mic()
    elif cmd == "/listen":
        toggle_voice_mode()
    elif cmd == "/info":
        qprint("QClawCode: Q-Claw + Claw-Coder merged. Local AI Agent.")
    elif cmd == "/reset":
        agent.reset()
        qprint("Context reset.")
    elif cmd == "/compact":
        qprint("Compacting context... (stub)")
    elif cmd == "/model":
        if args:
            new_model = args[0]
            SETTINGS["model"] = new_model
            agent.model = new_model
            save_settings()
            qprint(f"Switched model to {new_model}")
        else:
            qprint(f"Current model: {SETTINGS['model']}")
    elif cmd == "/audit":
        qprint("Auditing code... (stub)")
    else:
        qprint(f"Unknown command: {cmd}")

    return True

def chat_repl(preload_pdfs=None):
    if preload_pdfs:
        for pdf in preload_pdfs:
            qprint(f"Ingesting {pdf}...")
            try:
                if pdf.endswith((".txt", ".md")):
                    count = agent.rag_store().ingest_text(pdf)
                else:
                    count = agent.rag_store().ingest_pdf(pdf)
                qprint(f"Ingested {count} chunks from {pdf}")
            except Exception as e:
                qprint(f"Failed to ingest {pdf}: {e}")

    refresh()
    while True:
        try:
            if IN_VOICE_MODE:
                qprint("Listening...")
                user_input = listen_mic()
                if user_input and user_input.lower() in ["stop", "exit", "quit"]:
                    toggle_voice_mode()
                    continue
                if user_input:
                    print(f"{ACCENT}> {user_input}{RESET}")
                    ask_agent(user_input)
                continue

            prompt_str = f"{ACCENT}> {RESET}"
            user_input = input(prompt_str).strip()
            if not user_input:
                continue

            if not handle_slash_command(user_input):
                ask_agent(user_input)

        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            qprint("Goodbye!")
            break
        except Exception as e:
            qprint(f"REPL error: {e}")

# -------------------------------
# MAIN ENTRY POINT
# -------------------------------
def main(args=None):
    save_history()

    if args is None:
        args = sys.argv[1:]

    # Extract global options manually so they don't break command parsing
    global_opts = {}
    i = 0
    while i < len(args):
        if args[i] in ["--model", "--embedding-model", "--db", "--collection", "--graph", "--top-k", "--depth"]:
            if i + 1 < len(args):
                key = args[i].lstrip("-").replace("-", "_")
                val = args[i+1]
                if key in ["top_k", "depth"]:
                    val = int(val)
                global_opts[key] = val
                args.pop(i)
                args.pop(i)
            else:
                args.pop(i)
        else:
            i += 1

    if global_opts:
        SETTINGS.update(global_opts)
        save_settings()
        agent.model = SETTINGS["model"]
        agent.embedding_model = SETTINGS["embedding_model"]
        agent.db_path = SETTINGS["db_path"]
        agent.collection = SETTINGS["collection"]
        agent._rag_store = None # Force rebuild with new settings

    if not args:
        args = ["chat"]

    cmd = args[0]
    cmd_args = args[1:]

    # Bare model name check (e.g., `claw llama3.2:3b`)
    if ":" in cmd and len(args) == 1:
        try:
            models = ollama.list().get("models", [])
            if any(cmd in (m.get("name", ""), m.get("model", "")) for m in models):
                SETTINGS["model"] = cmd
                agent.model = cmd
                save_settings()
                chat_repl()
                return
        except:
            pass

    if cmd == "chat":
        pdfs = []
        ui = "rich"
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] == "--pdf" and i + 1 < len(cmd_args):
                pdfs.append(cmd_args[i+1])
                i += 2
            elif cmd_args[i] == "--ui" and i + 1 < len(cmd_args):
                ui = cmd_args[i+1]
                i += 2
            else:
                i += 1
        if ui == "textual":
            start_textual_ui()
        else:
            chat_repl(preload_pdfs=pdfs)

    elif cmd == "models":
        try:
            models = ollama.list().get("models", [])
            for m in models:
                qprint(f"  {m.get('name', m.get('model'))}")
        except Exception as e:
            qprint(f"Failed to list models: {e}")

    elif cmd == "ingest":
        if not cmd_args:
            qprint("Usage: claw ingest <paths...>")
        else:
            for path in cmd_args:
                if os.path.isdir(path):
                    for root, _, files in os.walk(path):
                        for f in files:
                            ingest_file(os.path.join(root, f))
                else:
                    ingest_file(path)

    elif cmd == "ingest-code":
        if not cmd_args:
            qprint("Usage: claw ingest-code <file>")
        else:
            ingest_file(cmd_args[0], force_code=True)

    elif cmd == "ingest-pdf":
        if not cmd_args:
            qprint("Usage: claw ingest-pdf <file>")
        else:
            ingest_file(cmd_args[0], force_text=True)

    elif cmd == "search":
        if not cmd_args:
            qprint("Usage: claw search <query>")
        else:
            query = " ".join(cmd_args)
            chunks = agent.rag_store().search(query, top_k=SETTINGS.get("top_k", 4))

            # Graph reranking
            graph = KnowledgeGraph(SETTINGS.get("graph_path", os.path.join(CLAW_HOME, "graph.json")))
            reranked = []
            for c in chunks:
                source = c.metadata.get("source", "")
                graph_nodes = [n for n in graph.nodes if n.get("source") == source]
                boost = len(graph_nodes) * 0.1
                reranked.append((c, (c.distance or 1.0) - boost))

            reranked.sort(key=lambda x: x[1])

            for i, (c, score) in enumerate(reranked, 1):
                qprint(f"[{i}] {c.metadata.get('source')} (reranked dist: {score:.2f})")
                print(c.text[:500] + "...\n")

    elif cmd == "graph":
        if not cmd_args:
            qprint("Usage: claw graph <query>")
        else:
            query = " ".join(cmd_args)
            qprint(f"Searching graph for '{query}' (depth {SETTINGS.get('depth', 2)})...")
            graph = KnowledgeGraph(SETTINGS.get("graph_path", os.path.join(CLAW_HOME, "graph.json")))
            nodes = graph.search(query, depth=SETTINGS.get("depth", 2))
            for n in nodes:
                qprint(f"  Node: {n['name']} ({n['type']}) in {n['source']}")

    elif cmd == "summary":
        graph = KnowledgeGraph(SETTINGS.get("graph_path", os.path.join(CLAW_HOME, "graph.json")))
        n, e = graph.summary()
        qprint(f"Knowledge Graph: {n} nodes, {e} edges")

    elif cmd == "languages":
        show_languages()

    elif cmd == "setup":
        run_setup()

    elif cmd == "doctor":
        run_doctor()

    elif cmd in ["login", "logout", "whoami", "usage", "credits", "upgrade-plan", "topup"]:
        handle_account(cmd, cmd_args)

    elif cmd in ["help", "--help", "-h"]:
        print_help()

    else:
        qprint(f"Unknown command: {cmd}")
        print_help()

if __name__ == "__main__":
    main()
