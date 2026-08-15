"""
Terminal UI helpers for Claw Coder: rich formatting, model selection, tab titles.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
import math
import logging
import json
import platform
import subprocess
import urllib.request
import urllib.error
import ssl
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.spinner import Spinner
    from rich.status import Status
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

DEFAULT_TAB_PREFIX = "Claw-Coder"
_DISPLAY_MODE = "detailed"
_INPUT_SESSION: Any = None


def set_display_mode(mode: str) -> str:
    """Set the terminal density for the current chat session."""
    global _DISPLAY_MODE
    cleaned = mode.strip().lower()
    if cleaned not in {"compact", "detailed"}:
        raise ValueError("Display mode must be 'compact' or 'detailed'.")
    _DISPLAY_MODE = cleaned
    return _DISPLAY_MODE


def get_display_mode() -> str:
    return _DISPLAY_MODE


def _console() -> "Console":
    if not RICH_AVAILABLE:
        raise RuntimeError("rich is not installed. Run: claw setup")
    return Console(highlight=False)
def print_print_goodbye():
    if RICH_AVAILABLE:
        _console().print("[dim]\nSee you next time · Claw-Coder: Push me to the limit™️[/dim]")
        sys.exit(130)

def set_terminal_title(title: str) -> None:
    """Set the terminal tab/window title (OSC 0);"""
    clean = re.sub(r"[\x00-\x1f\x7f]", "", title).strip()
    if not clean:
        clean = DEFAULT_TAB_PREFIX
    if len(clean) > 80:
        clean = clean[:77] + "..."
    # OSC 0 = icon + window title; OSC 2 = window title (fallback for some terminals)
    for sequence in (f"\033]0;{clean}\007", f"\033]2;{clean}\007"):
        sys.stdout.write(sequence)
    sys.stdout.flush()


def format_session_status(
    model: str,
    embedding_model: str,
    workspace_mode: str,
    message_count: int,
    plan_count: int,
    context_window: int,
) -> None:
    """Show local model, service, and session health without changing state."""
    ollama_state = "unavailable"
    model_count = "—"
    try:
        models = list_ollama_models()
        ollama_state = "healthy"
        model_count = str(len(models))
    except Exception:
        pass

    memory = "—"
    try:
        import psutil
        memory = f"{psutil.virtual_memory().available // (1024 * 1024)} MB free"
    except Exception:
        pass

    rows = [
        ("Chat model", model),
        ("Embedding model", embedding_model),
        ("Context window", f"{context_window:,} tokens"),
        ("Workspace", workspace_mode),
        ("Ollama", f"{ollama_state} · {model_count} local model(s)"),
        ("Memory", memory),
        ("Conversation", f"{message_count} message(s) · {plan_count} plan step(s)"),
        ("Display", get_display_mode()),
    ]
    if not RICH_AVAILABLE:
        print("\n".join(f"{label}: {value}" for label, value in rows))
        return
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    for label, value in rows:
        style = "green" if label == "Ollama" and ollama_state == "healthy" else "red" if label == "Ollama" else "white"
        table.add_row(label, Text(value, style=style))
    _console().print(Panel(table, title="[bold]Session status[/bold]", border_style="cyan"))


def print_plan_progress(plan: Sequence[Dict[str, Any]]) -> None:
    """Show a small progress panel after the agent creates or updates a plan."""
    if not plan:
        return
    symbols = {"completed": "✓", "in_progress": "●", "pending": "○"}
    if not RICH_AVAILABLE:
        for item in plan:
            state = str(item.get("status", "pending"))
            print(f"{symbols.get(state, '○')} {item.get('step', '')}")
        return
    table = Table.grid(padding=(0, 1))
    for item in plan:
        state = str(item.get("status", "pending"))
        color = {"completed": "green", "in_progress": "yellow"}.get(state, "dim")
        table.add_row(Text(symbols.get(state, "○"), style=color), Text(str(item.get("step", ""))))
    _console().print(Panel(table, title="[bold]Plan progress[/bold]", border_style="cyan"))


def describe_tool_action(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Return a useful, short progress label without exposing full arguments."""
    labels = {
        "search_knowledge_base": "Searching indexed project knowledge",
        "search_knowledge_graph": "Tracing code relationships",
        "search_code": "Searching repository files",
        "run_terminal": "Running a terminal command",
        "run_tests": "Running tests",
        "read_files": "Reading files",
        "edit_file": "Editing a file",
        "apply_patch": "Applying a patch",
        "manage_plan": "Updating the plan",
        "search_stuff": "Searching current information",
        "ingest_paths_knowledge": "Indexing project files",
    }
    label = labels.get(tool_name, tool_name.replace("_", " ").capitalize())
    target = arguments.get("path") or arguments.get("query") or arguments.get("command")
    if target:
        clean_target = " ".join(str(target).split())
        return f"{label}: {clean_target[:72]}"
    return label


def print_recovery_guidance(error: str) -> None:
    """Show actionable recovery steps for common local-service failures."""
    lowered = error.lower()
    if not any(word in lowered for word in ("ollama", "connection", "terminated", "refused")):
        print_error(error)
        return
    guidance = (
        f"{error}\n\n"
        "Recovery: run `/status`. Claw-Coder will try to restart Ollama automatically; "
        "if it remains unavailable, run `ollama serve` in another terminal and retry. "
        "On a small Codespace, use a smaller model or wait for memory to be released."
    )
    if RICH_AVAILABLE:
        _console().print(Panel(guidance, title="[bold red]Ollama needs attention[/bold red]", border_style="red"))
    else:
        print(guidance)


def copy_to_clipboard(text: str) -> tuple[bool, str]:
    """Copy text when a platform clipboard program is available."""
    if not text:
        return False, "There is no assistant response to copy yet."
    commands = (["pbcopy"], ["clip"]) if platform.system() == "Windows" else (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"])
    for command in commands:
        try:
            subprocess.run(command, input=text, text=True, check=True, capture_output=True)
            return True, "Copied the latest response to your clipboard."
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False, "Clipboard integration is unavailable here. Select the rendered code block and copy it from the terminal."


def language_for_path(path: str) -> str:
    """Infer a Rich lexer name from a file path for edit previews."""
    extension = Path(path).suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript",
        ".tsx": "tsx", ".json": "json", ".md": "markdown", ".html": "html",
        ".css": "css", ".scss": "scss", ".sh": "bash", ".yml": "yaml", ".yaml": "yaml",
        ".toml": "toml", ".xml": "xml", ".java": "java", ".go": "go", ".rs": "rust",
        ".sql": "sql", ".rb": "ruby", ".php": "php", ".c": "c", ".cpp": "cpp",
    }.get(extension, "text")


def render_edit_preview(tool_name: str, arguments: Dict[str, Any]) -> None:
    """Render the proposed write before an edit tool is executed."""
    if not RICH_AVAILABLE or tool_name not in {"edit_file", "apply_patch", "create_file", "gnu_patch", "git_apply_patch"}:
        return
    path = str(arguments.get("path") or arguments.get("file_path") or "workspace")
    content = str(arguments.get("patch") or arguments.get("content") or arguments.get("target") or "")
    if not content:
        _console().print(f"[yellow]Preparing {tool_name.replace('_', ' ')} for {path}[/yellow]")
        return
    max_preview = 8000
    if len(content) > max_preview:
        content = content[:max_preview] + "\n… preview truncated …"
    language = "diff" if tool_name in {"apply_patch", "gnu_patch", "git_apply_patch"} else language_for_path(path)
    syntax = Syntax(content, language, theme="monokai", line_numbers=True, word_wrap=True)
    _console().print(
        Panel(
            syntax,
            title=f"[bold yellow]Proposed {tool_name.replace('_', ' ')}[/bold yellow] · {path}",
            subtitle=Text(language, style="bold cyan"),
            subtitle_align="right",
            border_style="yellow",
        )
    )


def render_markdown_response(text: str) -> None:
    """Render prose plus fenced code blocks, with the language shown at bottom-right."""
    if not RICH_AVAILABLE or "```" not in text:
        if RICH_AVAILABLE:
            _console().print(Markdown(text, code_theme="monokai"))
        else:
            print(text)
        return

    parts = re.split(r"```([A-Za-z0-9_+.-]*)\n?([\s\S]*?)```", text)
    for index in range(0, len(parts), 3):
        prose = parts[index].strip()
        if prose:
            _console().print(Markdown(prose, code_theme="monokai"))
        if index + 2 < len(parts):
            language = parts[index + 1].strip() or "text"
            code = parts[index + 2].strip("\n")
            _console().print(
                Panel(
                    Syntax(code, language, theme="monokai", line_numbers=True, word_wrap=True),
                    subtitle=Text(language, style="bold cyan"),
                    subtitle_align="right",
                    border_style="bright_black",
                )
            )


def conversation_title_from_message(message: str, max_len: int = 40) -> str:

    """Derive a short tab title from the user's first message using cloud API or local Ollama."""
    text = " ".join(message.strip().split())
    if not text:
        return DEFAULT_TAB_PREFIX

    text = re.sub(r"^[/!@#]+\s*", "", text)
    
    # Try to use cloud API for terminal naming first
    try:
        session_path = Path.home() / ".claw-coder" / "session.json"
        if session_path.exists():
            token_data = json.loads(session_path.read_text(encoding="utf-8"))
            token = token_data.get("access_token", "")
            if token:
                api_url = os.getenv("RATE_LIMIT_API_URL", "https://claw-coder-3.onrender.com")
                
                ssl_context = ssl.create_default_context()
                try:
                    import certifi
                    ssl_context = ssl.create_default_context(cafile=certifi.where())
                except ImportError:
                    pass
                
                request_data = json.dumps({"message": text}).encode("utf-8")
                request = urllib.request.Request(
                    f"{api_url}/terminal-name",
                    data=request_data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                
                try:
                    with urllib.request.urlopen(request, timeout=30, context=ssl_context) as resp:
                        response = json.loads(resp.read().decode("utf-8"))
                        if response.get("status") == "ok":
                            generated_title = response.get("title", "").strip()
                            if generated_title:
                                return f"{DEFAULT_TAB_PREFIX} · {generated_title}"
                        # If status is error, fall through to local generation
                        logging.debug(f"Cloud terminal naming returned error: {response.get('message')}")
                except urllib.error.HTTPError as exc:
                    # Fall back to local generation if cloud fails
                    logging.debug(f"Cloud terminal naming HTTP error: {exc}")
                except Exception as exc:
                    # Fall back to local generation if cloud fails
                    logging.debug(f"Cloud terminal naming error: {exc}")
    except Exception as exc:
        # Fall back to local generation if session or cloud fails
        logging.debug(f"Cloud API session/check failed: {exc}")
    
    # Fall back to local Ollama for AI generation
    try:
        import ollama
        
        # Get available models and use the smallest one to avoid resource issues
        available_models = []
        try:
            models_response = ollama.list()
            raw_models = getattr(models_response, "models", None)
            if raw_models is None and isinstance(models_response, dict):
                raw_models = models_response.get("models", [])
            
            for item in raw_models or []:
                if isinstance(item, dict):
                    name = item.get("model") or item.get("name")
                else:
                    name = getattr(item, "model", None) or getattr(item, "name", None)
                if name:
                    available_models.append(name)
        except Exception:
            pass
        
        # Prefer small models, fall back to any available model
        preferred_models = ["llama3.2:1b", "llama3.2:3b", "llama3.2", "qwen2.5:0.5b", "qwen2.5:1b"]
        model_to_use = None
        for preferred in preferred_models:
            if preferred in available_models:
                model_to_use = preferred
                break
        
        if not model_to_use and available_models:
            model_to_use = available_models[0]
        
        if not model_to_use:
            # No models available, use simple fallback
            raise Exception("No ollama models available")
        
        prefix_prompt = f"""Generate a SHORT terminal title (max 3 words) for this user message: "{text}"

Rules:
- Maximum 3 words, preferably 1-2 words
- First letter of each word capitalized
- Simple, direct, brief, and really straight to the point
- If nonsense or greeting, return "Chat"
- Focus on the main action or topic
- Examples: "Code Review", "Bug Fix", "API Setup", "Data Analysis", "Refactor", "Debug", "New Feature"
- For greetings: "Chat"
- For questions: "Help", "Question", "Debug"

Return ONLY the title, nothing else."""
        
        response = ollama.chat(
            model=model_to_use,
            messages=[{"role": "user", "content": prefix_prompt}],
            options={"timeout": 30}  # Shorter timeout for terminal naming
        )
        
        # Handle different response formats from ollama
        if hasattr(response, 'message'):
            generate_title = response.message.content.strip()
        elif isinstance(response, dict):
            generate_title = response.get("message", {}).get("content", "").strip()
        else:
            generate_title = str(response).strip()
        
        # Clean up the response - remove any extra text
        generate_title = generate_title.replace('"', '').replace("'", "").strip()
        
        # Limit to max 3 words
        words = generate_title.split()[:3]
        generate_title = " ".join(words)
        
        # Capitalize first letter of each word
        generate_title = " ".join(word.capitalize() for word in words)
        
        # Ensure it's not too long
        max_len = 20
        if len(generate_title) > max_len:
            generate_title = generate_title[:max_len].rsplit(" ", 1)[0] + "…"
        
        # Only return if we got a meaningful title
        if generate_title and len(generate_title) > 2:
            return f"{DEFAULT_TAB_PREFIX} · {generate_title}"
        
    except Exception as e:
        # Log the error for debugging but continue to fallback
        logging.debug(f"LLM terminal naming failed: {e}")
    
    # Final fallback - use truncated text
    max_len = 40  # Max length for fallback titles
    if len(text) <= max_len:
        title = text
    else:
        cut = text[:max_len]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        title = cut + "…"

    return f"{DEFAULT_TAB_PREFIX} · {title}"

def pull_model_with_progress(model_name: str) -> None:
    if not RICH_AVAILABLE:
        import ollama
        print(f"{model_name} not installed, pulling...")
        try:
            for chunk in ollama.pull(model_name, stream=True):
                status = chunk.get("status", "")
                if status:
                    print(f"  {status}")
            print(f"✓ {model_name} installed.")
        except Exception as e:
            print(f"✗ Failed to pull {model_name}: {e}")
        return

    from rich.progress import (
        Progress, SpinnerColumn, TextColumn, BarColumn,
        DownloadColumn, TransferSpeedColumn, TimeRemainingColumn,
    )
    import ollama

    _console().print(f"[bold green] ❌ {model_name} not installed[/bold green]")
    _console().print()

    try:
        with Progress(
            SpinnerColumn(spinner_name="runner"),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(f"Internalizing {model_name}", total=None)
            current_phase: Optional[str] = None
            last_digest: Optional[str] = None

            for chunk in ollama.pull(model_name, stream=True):
                total = chunk.get("total")
                completed = chunk.get("completed")
                status_text = chunk.get("status", "")
                digest = chunk.get("digest", "")

                # Improved phase detection to prevent duplicate progress bars
                phase_key = status_text.split()[0] if status_text else ""
                digest_key = digest[:12] if digest else ""
                
                # Only reset when we actually move to a new phase or new digest
                if phase_key != current_phase or (digest_key and digest_key != last_digest):
                    current_phase = phase_key
                    last_digest = digest_key
                    progress.reset(task, total=total, completed=0)

                label = friendly_status(status_text)
                if total:
                    progress.update(task, total=total, completed=completed or 0,
                                    description=f"{model_name} — {label}")
                else:
                    progress.update(task, description=f"{model_name} — {label}")

        print(f"✓ {model_name} installed.")
    except Exception as e:
        _console().print(f"[bold red]✗ Failed to pull {model_name}: {e}[/bold red]")
        raise

def friendly_status(status_text: str) -> str:
    if not status_text:
        return "Sifting"
    mapping = {
        "verifying sha256 digest": "Verifying (already downloaded, checking integrity)…",
        "verifying sha256 digest": "Verifying download…",
        "writing manifest": "Finalizing…",
        "removing any unused layers": "Cleaning up…",
        "success": "Done",
    }
    if status_text in mapping:
        return mapping[status_text]
    if status_text.startswith("Internalizing") and re.search(r"[0-9a-f]{6,}", status_text):
        return "Dribbling tasks for AI model"
    return status_text.capitalize()

def list_ollama_models() -> List[Dict[str, Any]]:
    import ollama

    try:
        response = ollama.list()
    except Exception as exc:
        raise RuntimeError(
            "Could not reach Ollama. Start it with: ollama serve"
        ) from exc

    raw_models = getattr(response, "models", None)
    if raw_models is None and isinstance(response, dict):
        raw_models = response.get("models", [])

    models: List[Dict[str, Any]] = []
    for item in raw_models or []:
        if isinstance(item, dict):
            name = item.get("model") or item.get("name")
            size = item.get("size")
        else:
            name = getattr(item, "model", None) or getattr(item, "name", None)
            size = getattr(item, "size", None)
        if not name:
            continue
        models.append({"name": name, "size": size})
    return sorted(models, key=lambda entry: entry["name"])


def validate_ollama_model(model: str) -> str:
    model = model.strip()
    if not model:
        raise ValueError("Model name cannot be empty.")
    if any(char.isspace() for char in model):
        raise ValueError(
            f"Invalid Ollama model name: {model!r}. "
            "Use names like llama3.2:1b or qwen2.5-coder:7b without spaces."
        )
    available = {entry["name"] for entry in list_ollama_models()}
    if model in available:
        return model
    # Ollama tags often omit :latest
    if f"{model}:latest" in available:
        return f"{model}:latest"
    try:
        pull_model_with_progress(model)
    except Exception:
        raise ValueError(
            f"Could not install {model}."
            f"Try manually pulling it: ollama pull {model}, or check the spelling."
        ) from None
    available_after = {entry["name"] for entry in list_ollama_models()}
    if model in available_after:
        return model
    if f"{model}:latest" in available_after:
        return f"{model}:latest"
    raise ValueError(
        f"Even after creating a request for {model} its still not available this can be caused lack of the model in general."
        f"Try manually pulling it: ollama pull {model} or check the spelling of the model"
    )



def resolve_chat_model(explicit: Optional[str] = None) -> str:
    candidates = [
        explicit,
        os.getenv("CLAW_MODEL"),
        os.getenv("OLLAMA_MODEL"),
    ]
    for candidate in candidates:
        if candidate and candidate.strip():
            return validate_ollama_model(candidate.strip())
    return pick_chat_model_interactive()


def pick_chat_model_interactive() -> str:
    models = list_ollama_models()
    if not models:
        pull_model_with_progress(models)
    if not RICH_AVAILABLE:
        print("Available Ollama models:")
        for index, entry in enumerate(models, start=1):
            print(f"  {index}. {entry['name']}")
        while True:
            choice = input("Pick a model number or type a model name: ").strip()
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(models):
                    return models[idx - 1]["name"]
            else:
                return validate_ollama_model(choice)

    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=4)
    table.add_column("Model", style="green")
    table.add_column("Size", style="dim", justify="right")
    for index, entry in enumerate(models, start=1):
        size = entry.get("size")
        size_label = _format_bytes(size) if size else "—"
        table.add_row(str(index), entry["name"], size_label)

    _console().print(Panel(table, title="[bold]Local models[/bold]", border_style="cyan"))
    while True:
        choice = Prompt.ask(
            "[bold cyan]Model[/bold cyan]",
            default=models[0]["name"],
        ).strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(models):
                return models[idx - 1]["name"]
        try:
            return validate_ollama_model(choice)
        except ValueError as exc:
            _console().print(f"[red]{exc}[/red]")


def _format_bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} PB"


def show_simple_welcome_box():
    """Display a simple static welcome box."""
    if not RICH_AVAILABLE:
        print("Welcome to Claw-Coder!")
        return
    
    width = 60
    height = 8
    
    # Box border characters
    corners = ['╭', '╮', '╰', '╯']
    horizontal = '─'
    vertical = '│'
    
    # Build the box
    lines = []
    
    # Top border
    top_line = corners[0] + horizontal * (width - 2) + corners[1]
    lines.append(top_line)
    
    # Empty lines with borders
    empty_line = vertical + ' ' * (width - 2) + vertical
    for _ in range(height - 2):
        lines.append(empty_line)
    
    # Bottom border
    bottom_line = corners[2] + horizontal * (width - 2) + corners[3]
    lines.append(bottom_line)
    
    # Add welcome text in the center
    welcome_text = "Welcome to Claw-Coder"
    text_x = (width - len(welcome_text)) // 2
    text_y = height // 2
    
    if 0 <= text_y < len(lines):
        line = list(lines[text_y])
        for idx, char in enumerate(welcome_text):
            if 0 <= text_x + idx < len(line):
                line[text_x + idx] = char
        lines[text_y] = ''.join(line)
    
    box = '\n'.join(lines)
    _console().print(f"[bold cyan]{box}[/bold cyan]")
    _console().print()

def print_banner(model: str, embedding_model: str) -> None:
    if not RICH_AVAILABLE:
        print(f"Claw-Coder — model: {model} | embeddings: {embedding_model}")
        return
    if get_display_mode() == "compact":
        _console().print(f"[bold cyan]Claw-Coder[/bold cyan]  [dim]chat {model} · embed {embedding_model}[/dim]")
        _console().print("[dim]Commands: /help · /status · /display detailed[/dim]")
        return
    os.system("clear")
    # Show simple welcome box
    show_simple_welcome_box()
    
    body = Text()
    body.append("OpenMindedAI's Claw Coder\n", style="bold cyan")
    body.append(f"chat  {model}\n", style="white")
    body.append(f"embed {embedding_model}\n", style="dim")
    body.append("\nCommands: /help /status /models /copy /display <compact|detailed>  exit\n", style="dim italic")
    _console().print(Panel(body, border_style="cyan", padding=(1, 2)))


def print_models_table(models: Sequence[Dict[str, Any]]) -> None:
    if not RICH_AVAILABLE:
        for entry in models:
            print(f"  {entry['name']}")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Model", style="green")
    table.add_column("Size", justify="right", style="dim")
    for entry in models:
        table.add_row(entry["name"], _format_bytes(entry.get("size")))
    _console().print(table)


def print_user_prompt() -> None:
    if RICH_AVAILABLE:
        _console().print()
        _console().print("[bold green]You[/bold green]", end=" ")
    else:
        print("\nYou> ", end="", flush=True)


def read_user_input() -> str:
    if RICH_AVAILABLE:
        return Prompt.ask("", default="").strip()
    return input("").strip()


def read_multiline_input() -> str:
    """Read input with persistent history; Enter sends and Alt+Enter adds a line."""
    try:
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.shortcuts import PromptSession
        from prompt_toolkit.history import FileHistory
        
        # Custom key bindings
        kb = KeyBindings()
        
        @kb.add(Keys.Enter)
        def _(event):
            """Send input on Enter."""
            event.app.exit(result=event.app.current_buffer.text)

        @kb.add(Keys.Escape, Keys.Enter)
        def _(event):
            """Insert a newline with Alt+Enter."""
            event.app.current_buffer.insert_text("\n")
        
        global _INPUT_SESSION
        if _INPUT_SESSION is None:
            history_path = Path.home() / ".claw-coder" / "chat_history"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            _INPUT_SESSION = PromptSession(key_bindings=kb, history=FileHistory(str(history_path)))
        
        result = _INPUT_SESSION.prompt(
            '❭ ',
            multiline=True,
            enable_suspend=True,
            bottom_toolbar="Enter send · Alt+Enter newline · ↑/↓ history · /help commands",
        )
        
        return result.strip()
        
    except Exception as e:
        # Fallback to simple input
        return read_user_input()


def open_editor_for_input(initial_text: str = "") -> str:
    """Open the system's default editor for multi-line input editing."""
    import tempfile
    import subprocess
    import os
    import platform
    
    # Create a temporary file with the initial text
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as temp_file:
        temp_file.write(initial_text)
        temp_file_path = temp_file.name
    
    try:
        # Determine the editor to use
        editor = os.environ.get('EDITOR')
        if not editor:
            # Fallback to common editors based on platform
            if platform.system() == 'Windows':
                editor = 'notepad'
            else:
                # Try common editors in order of preference
                for potential_editor in ['vim', 'nano', 'vi', 'code', 'emacs']:
                    try:
                        subprocess.run(['which', potential_editor], check=True, capture_output=True)
                        editor = potential_editor
                        break
                    except subprocess.CalledProcessError:
                        continue
                
                # If no editor found, default to vi
                if not editor:
                    editor = 'vi'
        
        # Clear the screen for better editor experience
        if platform.system() != 'Windows':
            os.system('clear')
        
        # Open the editor
        if platform.system() == 'Windows':
            subprocess.call([editor, temp_file_path])
        else:
            # For Unix-like systems, use the terminal
            subprocess.call([editor, temp_file_path])
        
        # Read the edited content
        with open(temp_file_path, 'r') as file:
            edited_text = file.read()
        
        return edited_text.strip()
    
    except Exception as e:
        # If editor fails, fall back to regular input
        if RICH_AVAILABLE:
            _console().print(f"[red]Editor failed: {e}. Falling back to regular input.[/red]")
        else:
            print(f"Editor failed: {e}. Falling back to regular input.")
        return read_user_input()
    
    finally:
        # Clean up the temporary file
        try:
            os.unlink(temp_file_path)
        except OSError:
            pass


def print_assistant_start() -> None:
    if RICH_AVAILABLE:
        _console().print("[bold cyan]Claw-Coder[/bold cyan]")
    else:
        print("Claw> ", end="", flush=True)


def print_assistant_response(text: str) -> None:
    if not text:
        return
    if RICH_AVAILABLE:
        render_markdown_response(text)
        if "```" in text:
            _console().print("[dim]Tip: use /copy to copy this response, or select a code block in the terminal.[/dim]")
    else:
        print(text)


def print_status(message: str) -> None:
    if RICH_AVAILABLE:
        _console().print(f"[dim]{message}[/dim]")
    else:
        print(message)


def prompt_workspace_target() -> str:
    if RICH_AVAILABLE:
        body = Text()
        body.append("Paste your SSH connection details to connect to a remote workspace.\n\n", style="bold cyan")
        body.append("Supported formats:\n", style="bold yellow")
        body.append("• GitHub Codespaces: ssh cs.your-codespace-name\n", style="dim")
        body.append("• Regular SSH: ssh user@hostname\n", style="dim")
        body.append("• With port: ssh user@hostname:port\n", style="dim")
        body.append("• IP address: ssh user@192.168.1.1\n", style="dim")
        body.append("• SSH alias: your-ssh-config-alias\n", style="dim")
        body.append("• Codespaces URL: https://github.com/codespaces/...\n\n", style="dim")
        body.append("Claw will configure SSH, prepare the remote backend, and keep this chat open.\n", style="dim")
        body.append("The serve should be warm  because it will cause a long delay or error for the workspace feature.", style="dim")
        _console().print(Panel(body, title="[bold]Remote Workspace Connection[/bold]", border_style="cyan", padding=(1, 2)))
        return Prompt.ask("[bold cyan]SSH Target[/bold cyan]").strip()
    print("Remote Workspace Connection")
    print("Paste your SSH connection details:")
    print("• GitHub Codespaces: ssh cs.your-codespace-name")
    print("• Regular SSH: ssh user@hostname")
    print("• With port: ssh user@hostname:port")
    print("• IP address: ssh user@192.168.1.1")
    print("• SSH alias: your-ssh-config-alias")
    print("• Codespaces URL: https://github.com/codespaces/...")
    return input("SSH Target: ").strip()


def print_error(message: str) -> None:
    if RICH_AVAILABLE:
        _console().print(f"[bold red]Error:[/bold red] {message}")
    else:
        print(f"Error: {message}")


def ask_user_selection(question: str, options: List[str], default_index: int = 0) -> int:
    """
    Interactive selection menu with keyboard navigation.
    
    Args:
        question: The question/prompt to display
        options: List of option strings to display
        default_index: Default selected option index
    
    Returns:
        Selected option index
    """
    if not RICH_AVAILABLE:
        # Fallback to simple input for non-rich environments
        print(f"\n{question}")
        for i, option in enumerate(options, 1):
            print(f"  {i}. {option}")
        while True:
            try:
                choice = input(f"Select option (1-{len(options)}): ").strip()
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(options):
                        return idx
                print(f"Please enter a number between 1 and {len(options)}")
            except (EOFError, KeyboardInterrupt):
                return default_index
    
    from rich.panel import Panel
    from rich.text import Text
    
    selected_index = default_index
    
    while True:
        # Build the menu display
        menu_text = Text()
        menu_text.append(f"{question}\n\n", style="bold cyan")
        
        for i, option in enumerate(options):
            prefix = "· " if i != selected_index else "1 "
            style = "bold green" if i == selected_index else "dim"
            menu_text.append(f"{prefix}", style=style)
            menu_text.append(f"{option}\n", style=style)
        
        menu_text.append("\n", style="dim")
        menu_text.append("↑↓ select · ↵ confirm · esc cancel", style="dim italic")
        
        # Display the menu
        _console().clear()
        _console().print(Panel(menu_text, border_style="cyan", padding=(1, 2)))
        
        # Get user input
        try:
            # For simplicity in terminal environments, use number input
            # In a real implementation, this would use keyboard capture
            choice = Prompt.ask(
                "[bold cyan]Selection[/bold cyan]",
                default=str(selected_index + 1),
                show_default=False
            ).strip()
            
            if choice.lower() in {'q', 'quit', 'exit', 'esc'}:
                return default_index
            
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    return idx
            
            _console().print(f"[red]Invalid selection. Please enter 1-{len(options)}[/red]")
            
        except (EOFError, KeyboardInterrupt):
            return default_index


def print_print_goodbye() -> None:
    set_terminal_title(DEFAULT_TAB_PREFIX)
    if RICH_AVAILABLE:
        _console().print("\n[dim]Goodbye — run `claw chat` anytime.[/dim]\n")
    else:
        print("\nGoodbye — run `claw chat` anytime.\n")


class ChatSpinner:
    """Show activity while the agent thinks."""

    def __init__(self, label: str = "Being Creative...") -> None:
        self.label = label
        self._status: Optional[Status] = None

    def __enter__(self) -> "ChatSpinner":
        if RICH_AVAILABLE:
            self._status = _console().status(f"[cyan]{self.label}[/cyan]", spinner="moon")
            self._status.__enter__()
        else:
            print(f"{self.label}")
        return self

    def __exit__(self, *args: object) -> None:
        if self._status is not None:
            self._status.__exit__(*args)

    def update(self, label: str) -> None:
        self.label = label
        if self._status is not None:
            self._status.update(f"[cyan]{label}[/cyan]")


class ToolStatusDisplay:
    """Display real-time tool execution status like Devin."""
    
    def __init__(self) -> None:
        self._current_tool: Optional[str] = None
        self._status: Optional[Status] = None
        self._console = _console() if RICH_AVAILABLE else None
        self._started_at: Optional[float] = None
        
    def start_tool(self, tool_name: str, description: str = "", arguments: Optional[Dict[str, Any]] = None) -> None:
        """Start displaying status for a tool execution."""
        self._current_tool = tool_name
        self._started_at = time.perf_counter()
        if arguments:
            render_edit_preview(tool_name, arguments)
        if self._console and RICH_AVAILABLE:
            label = f"[bold blue]Running:[/bold blue] {tool_name}"
            if description:
                label += f" — {description}"
            self._status = self._console.status(label, spinner="dots")
            self._status.__enter__()
        elif not RICH_AVAILABLE:
            print(f"Running: {tool_name}")
            
    def update_tool(self, description: str) -> None:
        """Update the current tool status description."""
        if self._status and self._console:
            label = f"[bold blue]Running:[/bold blue] {self._current_tool}"
            if description:
                label += f" — {description}"
            self._status.update(label)
        elif not RICH_AVAILABLE:
            print(f"  {description}")
            
    def complete_tool(self, success: bool = True, result: str = "") -> None:
        """Mark the current tool as complete."""
        if self._status:
            self._status.__exit__(None, None, None)
            self._status = None
        
        elapsed = time.perf_counter() - self._started_at if self._started_at else 0.0
        duration = f" [dim]({elapsed:.1f}s)[/dim]"
        if self._console and RICH_AVAILABLE:
            if success:
                self._console.print(f"[green]✓[/green] {self._current_tool}{duration}")
            else:
                self._console.print(f"[red]✗[/red] {self._current_tool}{duration}")
            if result and get_display_mode() == "detailed":
                self._console.print(f"[dim]{result}[/dim]")
        elif not RICH_AVAILABLE:
            status = "✓" if success else "✗"
            print(f"{status} {self._current_tool} ({elapsed:.1f}s)")
            if result and get_display_mode() == "detailed":
                print(f"  {result}")
        
        self._current_tool = None
        self._started_at = None
        
    def __enter__(self) -> "ToolStatusDisplay":
        return self
        
    def __exit__(self, *args: object) -> None:
        if self._status:
            self._status.__exit__(*args)
            self._status = None
