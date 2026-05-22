"""
Standalone agent with web tools, terminal tools, PDF RAG, and Tree-sitter code RAG.

This file combines the useful parts of:
- agent.py: chat loop, web search, browser opening, terminal execution
- clock.py: PDF loading, chunking, ChromaDB storage, Ollama embeddings
- clock_tree_rag.py: multi-language Tree-sitter code chunking

Setup:
    pip install chromadb ollama ddgs pypdf tree-sitter tree-sitter-python
    pip install tree-sitter-javascript tree-sitter-typescript tree-sitter-json
    pip install tree-sitter-html tree-sitter-css tree-sitter-java tree-sitter-go tree-sitter-rust
    ollama serve
    ollama pull qwen3-embedding:4b
    ollama pull granite4.1:8b

Examples:
    python agent_rag.py languages
    python agent_rag.py code-chunks agent.py
    python agent_rag.py ingest-code agent.py
    python agent_rag.py ingest-pdf data/2509.24435v1.pdf
    python agent_rag.py search-kb "where is execute_tool?"
    python agent_rag.py chat
"""

from __future__ import annotations


import ollama
import importlib
import json
import logging
import argparse
import subprocess
import os
import hashlib
import tempfile
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Iterable, Optional, Tuple
from urllib.parse import urlparse
from tavily import TavilyClient
import shlex
from tree_sitter import Parser
from dotenv import load_dotenv
import re
from agent_knowledge import (
    DEFAULT_GRAPH_PATH,
    KnowledgeGraphStore,
    iter_supported_files as iter_knowledge_files,
    tree_sitter_available_languages as graph_tree_sitter_languages,
)

load_dotenv()

RATE_LIMIT_API_URL = os.getenv("RATE_LIMIT_API_URL", "http://localhost:8001")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    handlers=[logging.FileHandler("agent_rag.log"), logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


DEFAULT_CHAT_MODEL = ""
DEFAULT_EMBEDDING_MODEL = ""
DEFAULT_DB_PATH = "agent_rag_chroma_db"
DEFAULT_COLLECTION = "agent_mixed_knowledge"
DEFAULT_KNOWLEDGE_GRAPH_PATH = DEFAULT_GRAPH_PATH
DEFAULT_MEMORY_PATH = "agent_memory.json"
DEFAULT_PDF = Path(__file__).resolve().parent /"data" / "2509.24435v1.pdf" # you can place any document you desire for it to ingest then run python <file> ingest

SUPPORTED_LANGUAGES = ["python","javascript","typescript","tsx","html","css","json","yaml","toml","xml","bash","shell","c","cpp"
                       "java","kotlin","go","rust","ruby","php","lua","r","swift","dart","scala","haskell","perl","elixir","clojure"]
test_languages = {
    "python", "javascript", "typescript", "node", "go", "rust", "java", "ruby", "php", "csharp", "c", "cpp", "swift", "kotlin", "scala", "elixir", "lua", "perl", "r", "dart", "flutter", "ocaml",
    "clojure", "erlang", "crystal", "julia", "zig", "nim", "fortran", "bash", "shell"
}
test_commands = {
        "python": "pytest",
        "javascript": "npm test",
        "typescript": "npm test",
        "node": "npm test",
        "go": "go test ./...",
        "rust": "cargo test",
        "java": "./gradlew test",
        "ruby": "rspec",
        "php": "phpunit",
        "csharp": "dotnet test",
        "c": "make test",
        "cpp": "ctest",
        "swift": "swift test",
        "kotlin": "./gradlew test",
        "scala": "sbt test",
        "elixir": "mix test",
        "haskell": "stack test",
        "lua": "busted",
        "perl": "prove",
        "r": "Rscript -e 'testthat::test_dir(\"tests\")'",
        "dart": "dart test",
        "flutter": "flutter test",
        "ocaml": "dune runtest",
        "clojure": "lein test",
        "erlang": "rebar3 eunit",
        "crystal": "crystal spec",
        "julia": "julia --project -e 'using Pkg; Pkg.test()'",
        "zig": "zig test *.zig",
        "nim": "nimble test",
        "fortran": "ctest",
        "bash": "bats tests/",
        "shell": "bats tests/"
    }
LANGUAGES_SPECS:  Dict[str, Dict[str, str]] = {
    "python": {"module": "tree_sitter_python", "function": "language"},
    "javascript": {"module": "tree_sitter_javascript", "function": "language"},
    "typescript": {"module": "tree_sitter_typescript", "function": "language_typescript"},
    "tsx": {"module": "tree_sitter_typescript", "function": "language_tsx"},
    "json": {"module": "tree_sitter_json", "function": "language"},
    "html": {"module": "tree_sitter_html", "function": "language"},
    "csharp": {"module": "tree_sitter_c_sharp", "function": "language"},
    "cpp": {"module": "tree_sitter_cpp", "function": "language"},
    "c": {"module": "tree_sitter_c", "function": "language"},
    "go": {"module": "tree_sitter_go", "function": "language"},
    "java": {"module": "tree_sitter_java", "function": "language"},
    "rust": {"module": "tree_sitter_rust", "function": "language"},
    "ruby":{'module': 'tree_sitter_ruby', 'function': 'language'},
    "r": {"module": "tree_sitter_r", "function": "language"},


}
EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".rs": "rust",
    ".cjs": "javascript",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cs": "csharp",
    ".java": "java",
    ".c": "c",
    ".cc": "c++",
    ".cxx": 'c++',
    ".hpp": "c++",
    ".hh": "c++",
    ".hxx": "c++",
    ".htm": "html",
    ".html": "html",
    ".css": "css",
    ".ts": "typescript",
    ".json": "json",
    ".tsx": "tsx",
    ".rb": 'ruby',
    ".r": 'r',
    '.h': 'c',
    '.cpp': 'c++'
}

class ToolError(Exception):
    pass
WORKSPACE = Path("./workspace").resolve()
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
    status: Dict[str, Dict[str, Any]] = {}
    for language, spec in LANGUAGES_SPECS.items():
        try:
            module = importlib.import_module(spec["module"])
            getattr(module, spec["function"])
            status[language] = {"available": True, "module": spec["module"], "install": None}
        except Exception:
            package = spec["module"].replace("_", "-")
            status[language] = {
                "available": False,
                "module": spec["module"],
                "install": f"pip install {package}",
            }
    return status


def infer_language(path: str) -> Optional[str]:
    return EXTENSION_TO_LANGUAGE.get(Path(path).suffix.lower())


def load_tree_sitter_language(language_name: str):
    Language, Parser, Query, QueryCursor = require_tree_sitter()
    spec = LANGUAGES_SPECS.get(language_name)
    if not spec:
        supported = ", ".join(sorted(LANGUAGES_SPECS))
        raise RuntimeError(f"Unsupported language '{language_name}'. Supported: {supported}")

    try:
        module = importlib.import_module(spec["module"])
        language_fn = getattr(module, spec["function"])
    except Exception as exc:
        package = spec["module"].replace("_", "-")
        raise RuntimeError(
            f"Tree-sitter grammar for {language_name} is missing. Install it with: "
            f"pip install {package}"
        ) from exc

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
    docs: List[Document] = []
    for index, page in enumerate(pdf_reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            docs.append(
                Document(
                    page_content=text,
                    metadata={"source": str(pdf_path), "page": index, "kind": "pdf"},
                )
            )
    return docs


def split_documents(
    documents: Iterable[Document],
    chunk_size: int = 1200,
    chunk_overlap: int = 250,
) -> List[Document]:
    if chunk_size <= chunk_overlap:
        raise ValueError("chunk_size must be greater than chunk_overlap")

    chunks: List[Document] = []
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
            if end >= len(text):
                break
    return chunks


def fallback_code_chunks(path: str, text: str, language: str, chunk_size: int = 1200) -> List[Document]:
    chunks: List[Document] = []
    for index, start in enumerate(range(0, len(text), chunk_size)):
        end = min(start + chunk_size, len(text))
        content = text[start:end].strip()
        if content:
            chunks.append(
                Document(
                    page_content=content,
                    metadata={
                        "source": str(Path(path).resolve()),
                        "kind": "code",
                        "language": language,
                        "symbol_type": "text_chunk",
                        "symbol_name": f"chunk_{index}",
                        "start_byte": start,
                        "end_byte": end,
                    },
                )
            )
    return chunks


def query_for_language(language: str) -> Optional[str]:
    if language == "python":
        return """
        (function_definition name: (identifier) @name) @definition.function
        (class_definition name: (identifier) @name) @definition.class
        """
    if language == "javascript":
        return """
        (function_declaration name: (identifier) @name) @definition.class 
        (method_definition
        name: (property_identifier) @name) @definition.method
        (lexical_declaration (variable_declarator name: (identifier) @name
        value: [(arrow_function)])
        )@definition.lambda
        """
    if language in {"typescript", "tsx"}:
        return """
        (function_declaration name: (identifier) @name) @definition.function
        (class_declaration name: (type_identifier) @name) @definiton.class
        (method_definition name: (property_identifier) @name) @definition.method
        (interface_declaration name: (type_identifier) @name) @definition.interface
        (type_alias_declaration name: (type_identifier) @name) @definition.type
        (lexical_declaration (variable_declarator name: (identifier) @name value: [(arrow_function) (function)]
        )
        )@definition.lambda
        """
    if language == "java":
        return """
        (class_declaration name: (identifier) @name) @definition.class
        (interface_declaration name: (identifier) @name) @definition.interface
        (enum_declaration name: (identifier) @name) @definition.enum
        (method_declaration name: (identifier) @name) @definition.method
        (constructor_declaration name: (identifier) @name) @definition.constructor
        """
    if language == "go":
        return """
        (function_declaration name: (identifier) @name) @definition.function
        (method_declaration name: (field_identifier) @name) @definition.method
        (type_declaration (type_spec name: (type_identifier) @name
        )
        )@definition.type
        """
    if language == "rust":
        return """
        (function_item name: (identifier) @name) @definition.function
        (struct_item name: (type_identifier) @name) @definition.struct
        (enum_item name: (type_identifier) @name) @definition.enum
        (trait_item name: (type_identifier) @name) @definition.trait
        (impl_item) @definition.impl
        (macro_definition name: (identifier) @name) @definition.macro
        """
    if language == "c":
        return """
        (function_definition declarator: (function_declarator declarator: (identifier) @name)
        ) @definition.function
        (struct_specifier name: (type_identifier) @name) @definition.enum
        (union_specifier name: (type_identifier) @name) @definition.union
        """
    if language == "c++":
        return """
        (function_definition declarator: (identifier) @name)
        ) @definition.function
        (class_specifier name: (type_identifier) @name) @definition.class
        (struct_specifier name: (type_identifier) @name) @definition.struct
        (namespace_definition name: (namespace_identifier) @name) @definition.namespace
        """
    if language == "c#":
        return """
        (class_declaration name: (identifier) @name) @definition.class
        (interface_declaration name: (identifier) @name) @definition.interface
        @method_declaration name: (identifier) @name) @definition.method
        @struct_declaration name: (identifier) @name) @definition.struct
        (enum_declaration name: (identifier) @name) @definition.enum
        (constructor_declaration name: (identifier) @name) @definition.constructor
        """
    if language == "ruby":
        return """
        (method name: (identifier) @name) @definition.method
        (singleton_method name: (identifier) @name) @definition.singleton_method
        (class name: (constant) @name) @definition.class
        (module name: (constant) @name) @definition.module
        """
    if language == "r":
        return """
        (function_definition name: (identifier) @name) @definition.function
        (left_assignment name: (identifier) @name) @definition.variable
        """
    if language == "html":
        return """
        (element (start_tag (tag_name) @name)
        ) @definition.element
        """
    if language == "json":
        return """
        (pair key: (string) @name) @definition.key
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
    name_by_position: Dict[Tuple[int, int], str] = {}
    for name_node in captures.get("name", []):
        name_by_position[(name_node.start_byte, name_node.end_byte)] = node_text(source, name_node)

    chunks: List[Document] = []
    for capture_name, nodes in captures.items():
        if capture_name == "name":
            continue
        for node in nodes:
            symbol_name = "anonymous"
            for child in node.children:
                key = (child.start_byte, child.end_byte)
                if key in name_by_position:
                    symbol_name = name_by_position[key]
                    break
            content = node_text(source, node).strip()
            if not content:
                continue
            chunks.append(
                Document(
                    page_content=content,
                    metadata={
                        "source": str(file_path),
                        "kind": "code",
                        "language": detected_language,
                        "symbol_type": capture_name.replace("definition.", ""),
                        "symbol_name": symbol_name,
                        "start_byte": node.start_byte,
                        "end_byte": node.end_byte,
                        "start_point": list(node.start_point),
                        "end_point": list(node.end_point),
                        "has_error": bool(tree.root_node.has_error),
                    },
                )
            )

    return chunks or fallback_code_chunks(str(file_path), text, detected_language)


def stable_id(document: Document) -> str:
    source = document.metadata.get("source", "unknown")
    kind = document.metadata.get("kind", "unknown")
    start = document.metadata.get("chunk_start", document.metadata.get("start_byte", 0))
    name = document.metadata.get("symbol_name", "")
    digest = hashlib.sha256(
        f"{source}:{kind}:{start}:{name}:{document.page_content}".encode("utf-8")
    ).hexdigest()
    return digest[:24]


def ollama_embed(texts: Iterable[str], model: str = DEFAULT_EMBEDDING_MODEL) -> List[List[float]]:
    values = list(texts)
    if not values:
        return []
    try:
        response = ollama.embed(model=model, input=values)
    except Exception as exc:
        raise RuntimeError(
            "Ollama embedding failed. Make sure Ollama is running and the embedding "
            f"model is pulled: ollama pull {model}"
        ) from exc

    embeddings = response.get("embeddings")
    if embeddings:
        return embeddings
    raise RuntimeError("Ollama did not return embeddings.")


class MixedRAGStore:
    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        collection_name: str = DEFAULT_COLLECTION,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        chromadb = require_chromadb()
        self.embedding_model = embedding_model
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def add_documents(self, documents: List[Document]) -> int:
        if not documents:
            return 0

        ids = [stable_id(document) for document in documents]
        texts = [document.page_content for document in documents]
        metadatas = [document.metadata for document in documents]
        embeddings = ollama_embed(texts, model=self.embedding_model)
        self.collection.upsert(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        return len(documents)

    def ingest_pdf(self, path: str, chunk_size: int = 1200, chunk_overlap: int = 250) -> int:
        pages = load_pdf(path)
        chunks = split_documents(pages, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return self.add_documents(chunks)

    def ingest_code(self, path: str, language: Optional[str] = None) -> int:
        chunks = tree_sitter_code_chunks(path, language=language)
        return self.add_documents(chunks)

    def search(self, query: str, top_k: int = 4) -> List[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query cannot be empty")

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
            RetrievedChunk(
                text=text,
                metadata=metadata or {},
                distance=float(distance) if distance is not None else None,
            )
            for text, metadata, distance in zip(documents, metadatas, distances)
        ]

class Agent:
    def __init__(
        self,
        model: str = DEFAULT_CHAT_MODEL,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        max_steps: int = 8,
        rag_db_path: str = DEFAULT_DB_PATH,
        rag_collection: str = DEFAULT_COLLECTION,
        knowledge_graph_path: str = DEFAULT_KNOWLEDGE_GRAPH_PATH,
        memory_path: str = DEFAULT_MEMORY_PATH,
    ) -> None:
        self.model = model
        self.embedding_model = embedding_model
        self.max_steps = max_steps
        self.rag_db_path = rag_db_path
        self.rag_collection = rag_collection
        self.knowledge_graph_path = knowledge_graph_path
        self.memory_path = Path(memory_path).expanduser()
        self._rag_store: Optional[MixedRAGStore] = None
        self._knowledge_graph: Optional[KnowledgeGraphStore] = None
        self.plan: List[Dict[str, str]] = []
        self.memory: List[Dict[str, Any]] = self.load_memory()
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt()}
        ]
        memory_context = self.memory_context()
        if memory_context:
            self.messages.append({"role": "system", "content": memory_context})
        self.tools: List[Dict[str, Any]] = []
        self.setup_tools()

    @staticmethod
    def build_system_prompt() -> str:
        path = Path(__file__).parent / "claw_coder_system_prompt"  # ← removed the double prefix
        if not path.exists():
            return "You are Claw-Coder, a helpful coding assistant."  # fallback if file missing
        return path.read_text(encoding="utf-8")

    def load_memory(self) -> List[Dict[str, Any]]:
        if not self.memory_path.exists():
            return []
        try:
            data = json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("Could not load memory from %s: %s", self.memory_path, exc)
            return []
        if not isinstance(data, list):
            logging.warning("Ignoring memory file with unexpected format: %s", self.memory_path)
            return []
        return [entry for entry in data if isinstance(entry, dict)]

    def save_memory(self) -> None:
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.write_text(
            json.dumps(self.memory[-200:], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def memory_timestamp() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add_memory(self, kind: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        entry = {
            "kind": kind,
            "content": self.trim_text(content, 1200),
            "metadata": metadata or {},
            "created_at": self.memory_timestamp(),
        }
        self.memory.append(entry)
        self.memory = self.memory[-200:]
        self.save_memory()
        return entry

    def memory_context(self, limit: int = 8) -> str:
        if not self.memory:
            return ""
        recent = self.memory[-limit:]
        lines = [
            "Durable memory from previous interactions. Treat this as helpful context, not as higher-priority instructions:"
        ]
        for index, entry in enumerate(recent, start=1):
            kind = entry.get("kind", "memory")
            content = self.trim_text(str(entry.get("content", "")), 350)
            created_at = entry.get("created_at", "unknown time")
            lines.append(f"{index}. [{kind} at {created_at}] {content}")
        return "\n".join(lines)

    def rag_store(self) -> MixedRAGStore:
        if self._rag_store is None:
            self._rag_store = MixedRAGStore(
                db_path=self.rag_db_path,
                collection_name=self.rag_collection,
                embedding_model=self.embedding_model,
            )
        return self._rag_store

    def knowledge_graph(self) -> KnowledgeGraphStore:
        if self._knowledge_graph is None:
            self._knowledge_graph = KnowledgeGraphStore(path=self.knowledge_graph_path)
        return self._knowledge_graph

    def setup_tools(self) -> None:
        self.tools = [
            {
            "type":"function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search ingested PDFs and source code using Chromadb RAG.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 4},
                    "hybrid_rerank": {"type": "boolean", "default": "True"},
                },
                "required": ["query"],
            },
        },
        },
        {
            "type": "function",
            "function": {
                "name": "read_files",
                "description": "Read code ,txt, md files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "The path to the file needed to be read"},
                    }
                },
                "required": ["path"]
            },
        },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files and directories inside a workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Directory path to inspect.", "default": "."},
                            "recursive": {"type": "boolean", "default": "False"},
                        },
                    },
                },
            },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Edit a specified file by replacing, appending, prepending, or fully overwriting content in the directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file needed to be edited",
                        },
                        "operation": {
                            "type": "string",
                            "enum": [
                                "replace",
                                "append",
                                "prepend",
                                "overwrite"
                            ]
                        },
                       "target": {
                           "type": "string",
                           "description": (
                               "Text to replace when using replace operation"
                           )
                       },
                    "content": {
                        "type": "string",
                        "description": (
                            "New content to insert/write"
                        )
                    },
                },
                    "required": [
                        "path",
                        "operation",
                        "content"
                    ]
            },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": "Apply a unified diff patch to a file safely.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Target the file path"},
                        "patch": {"type": "string", "description": "Unified diff patch"},
                    },
                    "required": ["path", "patch"]
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_file",
                "description": "Create a new file safely and maturely",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "overwrite": {"type": "boolean", "default": False}
                    },
                },
                "required": ["path", "content"]
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_file",
                "description": "Delete a file maturely and responsibly",
                "parameters": {"path": {"type": "string"},
                               "recursive": {"type": "boolean", "default": "False"}
                               },
                "required": ["path"]
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Search codebase using regex",
                "parameters": {
                    "root": {
                        "type": "object",
                    },
                    "pattern": {"type": "string"},
                    "extensions": {"type": "array", "items": {"type": "string"}},
                    "max_results": {"type": "integer", "default": 50}
                },
                "required": ["root", "pattern"]
            },
        },
            {
                "type": "function",
                "function": {
                    "name": "run_tests",
                    "description": "Run project tests inside an isolated Docker container",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Project directory containing the tests."},
                            "language": {"type": "string", "enum": list(test_languages)},
                            "timeout": {"type": "integer", "default": 60}
                        },
                        "required": ["path", "language"]
                    },
                },
            },

        {
            "type": "function",
            "function": {
                "name": "search_knowledge_graph",
                "description": "Search the local knowledge graph for calls, imports, dependencies and entities",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 8},
                        "depth": {"type": "integer", "default": 2}
                    },
                    "required": ["query"]
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ingest_paths_knowledge",
                "description": "Ingest multiple local files and use them to help the user tell relationships between them",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "recursive": {"type": "boolean", "default": True},
                        "ingest_vector_rag": {"type": "boolean", "default": True},
                    },
                    "required": ["paths"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ingest_code_knowledge",
                "description": "Ingest a local source code file into the tree-sitter RAG",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "language": {"type": "string"},
                    },
                    "required": ["path"]
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ingest_pdf_knowledge",
                "description": "Ingest a pdf file, book, document, .pdf or .txt files into the pdf RAG",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"]
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_stuff",
                "description": "Search for the latest most accurate up-to-date information",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, },
                    "required": ["query"]
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_default_browser",
                "description": "Open the default browser",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}, },
                    "required": ["url"]
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_terminal",
                "description": "Run commands in the local terminal",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"},
                                   "timeout": {"type": "integer", "default": 30}},
                    "required": ["command"]
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "manage_plan",
                "description": "Monitor, create, update, and clear a concise plan for multi-step plan",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["set", "update", "list", "clear"],
                            "default": "list"
                        },
                        "tasks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "step": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed"],
                                        "default": "pending",
                                    },
                                },
                                "required": ["step"]
                            },
                        },
                        "index": {"type": "integer", "description": "Zero-based task index for update."},
                        "step": {"type": "integer", "description": "Replacement step text for update."},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Replacement status for update."
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_code_in_docker",
                "description": "Execute code in a sandboxed environment(docker isolated container) with no network and resource limits",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string", "enum": SUPPORTED_LANGUAGES,
                        "default": "python"},
                    },
                    "code": {"type": "string"},
                    "timeout": {"type": "integer", "default": 10},
                },
                "required": ["code"]
            },
        },
        {
            "type": "function",
            "function": {
                "name": "manage_memory",
                "description": "Store, list, search, or clear durable memories of the user's preferences, completed work, and interactions",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "search", "list", "clear"],
                            "default": "list",
                        },
                        "kind": {
                            "type": "string",
                            "description": "Memory category, such as user's preferences, completed_work, interactions or a quick note",
                            "default": "note",
                        },
                        "content": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            },
        },
            {
                "type": "function",
                "function": {
                    "name": "git_diff",
                    "description": "show git differences for modified, staged, or untracked files inside a repository",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "path to the git repository.", "default": "."},
                            "staged": {
                                "type": "boolean",
                                "description": "Show staged changes.", "default": False
                            },
                            "unified": {
                                "type": "string",
                                "description": "Number of context lines in diff output."
                            },
                            "file": {
                                "type": "string",
                                "description": "Optional single file to diff."
                            },
                        },
                    },
                },
            },
        {
            "type": "function",
            "function": {
                "name": "git_apply_patch",
                "description": "Apply a git unified diff patch to the repository",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {"type": "string"},
                        "patch": {"type": "string"},
                    },
                    "required": ["workspace", "patch"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gnu_patch",
                "description": "Apply a patch using GNU patch utility",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {"type": "string"},
                        "patch": {"type": "string"},
                    },
                    "required": ["workspace", "patch"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "extract_functions",
                "description": "Extract function symbols from a Python file using tree-sitter",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                    },
                    "required": ["file"],
                },
            },
        },
            {
                "type": "function",
                "function": {
                    "name": "ask_user",
                    "description": "Ask the user a question and wait for approval, clarfication, or additional instructions before continuing.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The question or approval request to show the user."
                            },
                            "context_file": {
                                "type": "string",
                                "description": "Optional file path related to the request."
                            },
                            "required_response": {
                                "type":"string",
                                "enum": ["approval", "text", "yes_no", "selection"],
                                "default": "text"
                            }
                        },
                        "required": ["question"]
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git status",
                    "description": "Get the current git repository status including modified, staged, deleted, and untracked files",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the git repo",
                                "default": "."
                            },
                            "short": {
                                "type": "boolean",
                                "description": "Use short git status format.",
                                "default": True
                            },
                            "branch": {
                                "type": "boolean",
                                "description": "Include branch information.",
                                "default": True
                            }
                        }
                    }
                }
            }
    ]

    @staticmethod
    def parse_tool_arguments(raw_args: Any) -> Dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                return {"value": raw_args}
        return {}

    @staticmethod
    def trim_text(value: str, limit: int = 700) -> str:
        return value if len(value) <= limit else value[:limit].rstrip() + "...(truncated)"

    def safe_path(self, path: str) -> Path:
        target = (WORKSPACE / path).resolve()
        if WORKSPACE not in target.parents and target != WORKSPACE:
            raise ToolError("Path escape detected")
        return target

    def _check_rate_limit(self, tool_name: str) -> Optional[str]:
        """
        Call the FastAPI rate-limit server.
        Returns None if allowed, or an error string if blocked/unreachable.
        Reads the user's JWT from ~/.claw-coder/session.json (written by claw login).
        """
        import json as _json
        import urllib.request as _req
        import urllib.error

        session_path = Path.home() / ".claw-coder" / "session.json"
        if not session_path.exists():
            return None  # no auth configured — skip limit check

        try:
            session = _json.loads(session_path.read_text(encoding="utf-8"))
            token = session.get("access_token", "")
            if not token:
                return None
        except Exception:
            return None

        payload = _json.dumps({"tool_name": tool_name}).encode("utf-8")
        request = _req.Request(
            f"{RATE_LIMIT_API_URL}/check",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with _req.urlopen(request, timeout=5) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
                remaining = data.get("remaining", "?")
                logging.info(
                    "Rate limit: %s — %s/%s used this month (%s remaining)",
                    tool_name,
                    data.get("used"),
                    data.get("limit"),
                    remaining,
                )
                return None  # allowed
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    detail = _json.loads(exc.read().decode("utf-8")).get("detail", {})
                    msg = detail.get("message", f"Rate limit exceeded for {tool_name}")
                except Exception:
                    msg = f"Rate limit exceeded for {tool_name}"
                return msg
            # any other HTTP error (401, 500) — fail open so the tool still runs
            logging.warning("Rate limit server error %s for %s — skipping", exc.code, tool_name)
            return None
        except Exception as exc:
            # server not running — fail open
            logging.warning("Rate limit server unreachable (%s) — skipping check", exc)
            return None

    def execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        try:
            # ── rate limit check ──────────────────────────────────────
            limit_error = self._check_rate_limit(tool_name)
            if limit_error:
                return json.dumps({"status": "error", "error": limit_error}, ensure_ascii=False)
            # ─
            if tool_name == "gnu_patch":
                return self._gnu_patch_tool(tool_input)
            if tool_name == "extract_functions":
                return self._extract_functions_tool(tool_input)
            if tool_name == "git_diff":
                return self._git_diff_tool(tool_input)
            if tool_name == "search_knowledge_base":
                return self._search_knowledge_base_tool(tool_input)
            if tool_name == "run_terminal":
                return self._run_terminal_tool(tool_input)
            if tool_name == "open_default_browser":
                return self._open_browser_tool(tool_input)
            if tool_name == "ingest_code_knowledge":
                return self._ingest_code_tool(tool_input)
            if tool_name == "ingest_pdf_knowledge":
                return self._ingest_pdf_tool(tool_input)
            if tool_name == "manage_memory":
                return self._manage_memory_tool(tool_input)
            if tool_name == "execute_code_in_docker":
                return self._execute_code_in_docker_tool(tool_input)
            if tool_name == "manage_plan":
                return self._manage_plan_tool(tool_input)
            if tool_name == "search_stuff":
                return self._search_stuff_tool(tool_input)
            if tool_name == "ingest_paths_knowledge":
                return self._ingest_paths_tool(tool_input)
            if tool_name == "search_knowledge_graph":
                return self._search_knowledge_graph_tool(tool_input)
            if tool_name == "edit_file":
                return self._edit_file_tool(tool_input)
            if tool_name == "apply_patch":
                return self._apply_patch_tool(tool_input)
            if tool_name == "search_code":
                return self._search_code_tool(tool_input)
            if tool_name == "run_tests":
                return self._run_tests_tool(tool_input)
            if tool_name == "delete_file":
                return self._delete_file_tool(tool_input)
            if tool_name == "create_file":
                return self._create_file_tool(tool_input)
            if tool_name == "list_files":
                return self._list_files_tool(tool_input)
            if tool_name == "read_files":
                return self._read_files_tool(tool_input)
            if tool_name == "ask_user":
                return self._ask_user_tool(tool_input)
            if tool_name == "git_apply_patch":
                return self._git_apply_patch_tool(tool_input)
            if tool_name == "git_status":
                return self._git_status_tool(tool_input)
            return json.dumps({"status": "error", "error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            logging.error("Tool failed: %s", exc)
            return json.dumps(
                {"status": "error", "tool": {tool_name}, "error": str(exc)},
                ensure_ascii=False,
            )
    def infer_language(self,path: Path) -> Optional[str]:
        return EXTENSION_TO_LANGUAGE.get(path.suffix.lower())

    def get_parser(self, path: Path) -> Parser:
        language = self.infer_language(path)
        if not language:
            raise ValueError(f"Unsupported file type: {path}")
        config = LANGUAGES_SPECS[language]
        module = importlib.import_module(config["module"])
        language_fn = getattr(module, config["function"])
        parser = Parser()
        parser.set_language(language_fn)
        return parser
    def _ask_user_tool(self, tool_input: Dict[str, Any]) -> str:
        question = tool_input["question"]
        context_file = tool_input.get("context_file")
        response = {
            "status": "waiting_for_user",
            "question": question,
            "context_file": context_file,
        }
        return json.dumps(response, ensure_ascii=False)
    def _git_status_tool(self, tool_input: Dict[str, Any]) -> str:
        repo_path = Path(tool_input.get("path", ".")).resolve()
        short = tool_input.get("short", True)
        branch = tool_input.get("branch", True)
        if not repo_path.exists():
            return json.dumps({
                "status": "error",
                "error": f"Repository path does not exist: {repo_path}"
            })
        command = [
            "git",
            "-C",
            str(repo_path),
            "status",
        ]
        if short:
            command.append("--short")
        if branch:
            command.append("--branch")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=15
            )
            return json.dumps({
                "status":(
                    "ok"
                    if result.returncode == 0
                    else "error"
                ),
                "repo": str(repo_path),
                "output": result.stdout,
                "stderr": result.stderr,
            }, ensure_ascii=False)
        except FileNotFoundError:
            return json.dumps({
                "status": "error",
                "error": "Git executable not found",
            })
        except subprocess.TimeoutExpired:
            return json.dumps({
                "status":"error",
                "error": "git status timed out"
            })



    def _git_diff_tool(self, tool_input: Dict[str, Any]) -> str:
        repo_path = Path(tool_input.get("path", ".")).resolve()
        staged = tool_input.get("staged", False)
        unified = int(tool_input.get("unified", 3))
        file_path = tool_input.get("file")
        if not repo_path.exists():
            return json.dumps({
                "status": "error",
                "error": f"Repository path does not exist: {repo_path}",
            })
        command = [
            "git",
            "-C",
            str(repo_path),
            "diff",
            f"--unified={unified}"
        ]
        if staged:
            command.append("--cached")
        if file_path:
            command.extend(["--", file_path])
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return json.dumps({
                    "status": (
                        "ok"
                        if result.returncode == 0
                        else "error"
                    ),
                    "repo": str(repo_path),
                    "diff": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                }, ensure_ascii=False)
            except FileNotFoundError:
                return json.dumps({
                    "status": "error",
                    "error": "Git executable not found."
            })
            except subprocess.TimeoutExpired:
                return json.dumps({
                    "status": "error",
                    "error": "git diff timed out."
                })
    def _extract_functions_tool(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        try:
            file_path = Path(tool_input["file"]).resolve()
            parser = self.get_parser(file_path)
            code = file_path.read_text(encoding="utf-8")
            tree = parser.parse(bytes(code, "utf-8"))

            root = tree.root_node
            functions = []

            def walk(node):
                if node.type == "function_definition":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        functions.append(
                            {
                                "name": code[name_node.start_byte:name_node.end_byte],
                                "line": node.start_point[0] + 1,
                            }
                        )
                for child in node.children:
                    walk(child)

            walk(root)
            return {
                "success": True,
                "functions": functions,
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc)
            }

    def _run_tests_tool(self, tool_input: Dict[str, Any]) -> str:
        path = Path(tool_input["path"]).resolve()
        language = tool_input["language"]
        timeout = min(max(1, tool_input.get("timeout", 60)), 300)
        if language not in test_commands:
            return json.dumps({
                "status": "error",
                "error": f"Unsupported language: {language}"
            })
        spec = LANGUAGES_SPECS[language]
        docker_command = [
            "docker",
            "run",
            "--rm",

            "--network", "none",
            "--memory", "512m",
            "--cpus", "2"
            "-v", f"{path}:/workspace",
            "-w", "/workspace",

            spec["image"],

            "sh",
            "-c",
            test_commands[language]
        ]
        try:
            result = subprocess.run (
                docker_command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return json.dumps({
                "status": (
                    "ok"
                    if result.returncode == 0
                    else "failed"
                )
            })
        except subprocess.TimeoutExpired:
            return json.dumps({
                "status": "timeout",
            })
    def _search_code_tool(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(tool_input["root"]).resolve()
        pattern = re.compile(tool_input["query"])
        max_results = tool_input.get("max_results", 50)
        matches = []

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines()):
                if pattern.search(line):
                    matches.append({
                        "file": str(path),
                        "line": i + 1,
                        "content": line.strip()
                    })
                    if len(matches) >= max_results:
                        return {"success": True, "matches": matches}
        return {"success": True, "matches": matches}
    def _list_files_tool(self, tool_input: Dict[str, Any]) -> str:
        path = Path(tool_input.get("path", ".")).resolve()
        recursive = tool_input.get("recursive", False)
        if not path.exists():
            return json.dumps({
                "status": "error",
                "error": f"Path does not exist: {path}"
            })
        files = []
        if recursive:
            for p in path.rglob("*"):
                files.append({
                    "path": str(p),
                    "type": "directory" if p.is_dir() else "file"
                })
        else:
            for p in path.iterdir():
                files.apppend({
                    "path": str(p),
                    "type": "directory" if p.is_dir() else "file"
                })
        return json.dumps({
            "status": "ok",
            "path": str(path),
            "files": files
        }, ensure_ascii=False)
    def _create_file_tool(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        try:
            path = tool_input["path"]
            content = tool_input["content"]
            overwrite = tool_input.get("overwrite", False)
            target = self.safe_path(path)
            if target.exists() and not overwrite:
                raise ToolError("File already exists")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"success": True, "path": str(target), "bytes_written": len(content.encode("utf-8"))}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}

    def _delete_file_tool(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        try:
            target = self.safe_path(tool_input["path"])
            if not target.exists():
                raise ToolError("File does not exist")
            if target.is_dir():
                raise ToolError("Refusing to delete directory")
            target.unlink()
            return {"success": True}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}


    def _search_knowledge_base_tool(self, tool_input: Dict[str, Any]) -> str:
        query = str(tool_input.get("query", "")).strip()
        top_k = int(tool_input.get("top_k", 4))
        hybrid_rerank = bool(tool_input.get("hybrid_rerank", True))
        if not query:
            return json.dumps({"status": "error", "error": "Missing query"})

        chunks = self.rag_store().search(query=query, top_k=max(top_k * 3, top_k))
        raw_chunks = [
            {
                "text": chunk.text,
                "metadata": chunk.metadata,
                "distance": chunk.distance,
            }
            for chunk in chunks
        ]
        if hybrid_rerank:
            hybrid = self.knowledge_graph().hybrid_context(query, raw_chunks, top_k=top_k)
            return json.dumps(
                {
                    "status": "ok",
                    "query": query,
                    "mode": "hybrid_graph_reranked_rag",
                    **hybrid,
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "status": "ok",
                "query": query,
                "mode": "vector_rag",
                "chunks": raw_chunks[:top_k],
            },
            ensure_ascii=False,
        )

    def _search_knowledge_graph_tool(self, tool_input: Dict[str, Any]) -> str:
        query = str(tool_input.get("query", "")).strip()
        top_k = int(tool_input.get("top_k", 8))
        depth = int(tool_input.get("depth", 2))
        if not query:
            return json.dumps({"status": "error", "error": "Missing query"})

        graph = self.knowledge_graph()
        results = graph.search(query, top_k=top_k)
        subgraph = graph.related_subgraph(query, depth=depth, limit=max(20, top_k * 6))
        return json.dumps(
            {
                "status": "ok",
                "query": query,
                "results": results,
                "subgraph": subgraph,
                "summary": graph.summary(),
            },
            ensure_ascii=False,
        )

    def _ingest_paths_tool(self, tool_input: Dict[str, Any]) -> str:
        raw_paths = tool_input.get("paths") or []
        if isinstance(raw_paths, str):
            paths = [raw_paths]
        else:
            paths = [str(path) for path in raw_paths]
        recursive = bool(tool_input.get("recursive", True))
        ingest_vector_rag = bool(tool_input.get("ingest_vector_rag", True))
        if not paths:
            return json.dumps({"status": "error", "error": "Missing paths"})

        graph_result = self.knowledge_graph().ingest_paths(paths, recursive=recursive)
        vector_added = 0
        vector_errors: List[Dict[str, str]] = []
        if ingest_vector_rag:
            for file_path in iter_knowledge_files(paths, recursive=recursive):
                language = infer_language(str(file_path))
                if not language:
                    continue
                try:
                    vector_added += self.rag_store().ingest_code(str(file_path), language=language)
                except Exception as exc:
                    vector_errors.append({"path": str(file_path), "error": str(exc)})

        return json.dumps(
            {
                "status": "ok",
                "graph": graph_result,
                "vector_chunks_added": vector_added,
                "vector_errors": vector_errors,
            },
            ensure_ascii=False,
        )

    def _ingest_code_tool(self, tool_input: Dict[str, Any]) -> str:
        path = str(tool_input.get("path", "")).strip()
        raw_language = tool_input.get("language")
        language = str(raw_language).strip() if raw_language is not None else None
        language = language or None
        if not path:
            return json.dumps({"status": "error", "error": "Missing path"})

        chunks_preview = tree_sitter_code_chunks(path, language=language)
        count = self.rag_store().add_documents(chunks_preview)
        try:
            self.knowledge_graph().ingest_paths([path], recursive=False)
        except Exception as exc:
            logging.warning("Knowledge graph ingest failed for %s: %s", path, exc)
        return json.dumps(
            {"status": "ok", "path": path, "chunks_added": count},
            ensure_ascii=False,
        )

    def _ingest_pdf_tool(self, tool_input: Dict[str, Any]) -> str:
        path = str(tool_input.get("path", "")).strip()
        if not path:
            return json.dumps({"status": "error", "error": "Missing path"})

        count = self.rag_store().ingest_pdf(path)
        return json.dumps(
            {"status": "ok", "path": path, "chunks_added": count},
            ensure_ascii=False,
        )

    def apply_patch(self, original_text: str, patch_text: str) -> str:

        original_lines = original_text.splitlines(keepends=True)
        patch_lines = patch_text.splitlines()
        result = list(original_lines)
        offset = 0
        i = 0

        while i < len(patch_lines):
            line = patch_lines[i]

            if line.startswith("---") or line.startswith("+++"):
                i += 1
                continue

            if line.startswith("@@"):

                m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if not m:
                    i += 1
                    continue

                orig_start = int(m.group(1)) - 1
                pos = orig_start + offset
                i += 1

                while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
                    current = patch_lines[i]
                    if current.startswith(" "):

                        pos += 1
                    elif current.startswith("+"):

                        result.insert(pos, current[1:] + "\n")
                        pos += 1
                        offset += 1
                    elif current.startswith("-"):

                        if pos < len(result):
                            result.pop(pos)
                            offset -= 1
                    i += 1
            else:
                i += 1

        return "".join(result)

    def _apply_patch_tool(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        try:
            path = self.safe_path(tool_input["path"])
            patch = tool_input["patch"]

            if not path.exists():
                return {"success": False, "error": "File not found"}

            original = path.read_text(encoding="utf-8")
            updated = self.apply_patch(original_text=original, patch_text=patch)

            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_text(original, encoding="utf-8")
            path.write_text(updated, encoding="utf-8")

            return {
                "success": True,
                "path": str(path),
                "backup": str(backup),
                "message": "Patch applied successfully",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}
    def _edit_file_tool(self, tool_input: Dict[str, Any]) -> str:
        path = Path(tool_input["path"]).resolve()
        operation = tool_input["operation"]
        content = tool_input["content"]
        target = tool_input.get("target")

        if not path.exists():
            return json.dumps({
                "status": "error",
                "error": f"File does not exist: {path}"
            })
        original = path.read_text(encoding="utf-8")
        if operation == "overwrite":
            updated = content
        elif operation == "append":
            updated = original + content
        elif operation == "prepend":
            updated = content + original

        elif operation == "replace":
            if target is None:
                return json.dumps({
                    "status": "error",
                    "error": "replace operation requires target",
                })
            if target not in original:
                return json.dumps({
                    "status": "error",
                    "error": "target text not found"
                })
            updated = original.replace(target, content, 1)
        else:
            return json.dumps({
                "status": "error",
                "error": f"Unknown operation: {operation}"
            })
        path.write_text(updated, encoding="utf-8")
        return json.dumps({
            "status": "ok",
            "path": str(path),
            "operation": operation,
        }, ensure_ascii=False)

    def _git_apply_patch_tool(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        workspace = Path(tool_input["workspace"]).resolve()
        patch = tool_input["patch"]

        result = subprocess.run(
            ["git", "-C", str(workspace), "apply", "--whitespace=nowarn"],
            input=patch,
            text=True,
            capture_output=True,
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr[-2000:],
            }
        return {"success": True, "message": "Patch applied"}

    def _gnu_patch_tool(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        workspace = Path(tool_input["workspace"]).resolve()
        patch = tool_input["patch"]

        result = subprocess.run(
            ["patch", "-p1"],
            cwd=workspace,
            input=patch,
            text=True,
            capture_output=True,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-2000:],  # same fix
            "stderr": result.stderr[-2000:],
        }
    def get_env(self, key: str) -> str:
        value = os.getenv(key)
        if not value:
            raise ValueError(f"Missing env var: {key}")
        return value
    def search_info(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        try:
            tavily = TavilyClient(api_key=self.get_env("TAVILY_API_KEY"))
            response = tavily.search(
                query=query,
                max_results=max_results,
                search_depth="advanced",
            )
            results = []
            for item in response["results"]:
                results.append({
                    "title": item["title"],
                    "url": item["url"],
                    "content": item["content"][:1000],
                })
            return {"success": True, "results": results}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _search_stuff_tool(self, tool_input: Dict[str, Any]) -> str:
        query = str(tool_input.get("query", "")).strip()
        if not query:
            return json.dumps({"status": "error", "error": "Missing query"})

        result = self.search_info(query)


        if not result.get("success"):
            return json.dumps({
                "status": "error",
                "query": query,
                "error": result.get("error", "Search failed. Check internet/search provider."),
            })

        return json.dumps({
            "status": "ok",
            "query": query,
            "results": result["results"],
        }, ensure_ascii=False)

    def _open_browser_tool(self, tool_input: Dict[str, Any]) -> str:
        url = str(tool_input.get("url", "")).strip()
        if not url:
            return json.dumps({"status": "error", "error": "Missing url"})
        if not urlparse(url).scheme:
            url = f"https://{url}"
        try:
            opened = bool(webbrowser.open(url, new=2))
        except Exception as exc:
            return json.dumps({"status": "error", "url": url, "error": str(exc)})
        if not opened:
            return json.dumps({"status": "error", "url": url, "error": "Browser could not open"})
        return json.dumps({"status": "ok", "url": url})

    @staticmethod
    def is_read_only_command(command: str) -> bool:
        try:
            parts = shlex.split(command)
        except ValueError:
            return False
        if not parts:
            return True
        if any(symbol in command for symbol in [">", ">>", "2>", "| tee", "&&", "||"]):
            return False
        if parts[0] in {"ls", "pwd", "whoami", "cat", "head", "tail", "grep", "find", "date", "echo", "wc"}:
            return True
        if parts[0] == "git" and len(parts) > 1:
            return parts[1] in {"status", "log", "show", "diff", "branch", "remote", "rev-parse"}
        if parts[0] in {"python", "python3"}:
            return any(flag in parts for flag in ("--version", "-V"))
        return False

    def needs_confirmation(self, command: str) -> bool:
        lowered = f" {command.strip().lower()} "
        high_risk = [
            "sudo ",
            " rm ",
            "rm -",
            "mv ",
            "cp ",
            "chmod ",
            "chown ",
            "git commit",
            "git push",
            "git reset",
            "git clean",
            "pip install",
            "pip uninstall",
        ]
        return any(marker in lowered for marker in high_risk) or not self.is_read_only_command(command)

    def ask_user_confirmation(self, command: str) -> bool:
        print("\nTool requested this terminal command:")
        print(f"  {command}")
        answer = input("Run this command? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    @staticmethod
    def decode_process_output(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def run_terminal(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=max(1, timeout),
            )
            return {
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "command": command,
                "stdout": self.decode_process_output(exc.stdout),
                "stderr": f"Command timed out after {timeout} seconds.",
                "returncode": 124,
            }

    def _run_terminal_tool(self, tool_input: Dict[str, Any]) -> str:
        command = str(tool_input.get("command", "")).strip()
        if not command:
            return json.dumps({"status": "error", "error": "Missing command"})
        timeout = int(tool_input.get("timeout", 30))
        if self.needs_confirmation(command) and not self.ask_user_confirmation(command):
            return json.dumps({"status": "cancelled", "command": command})

        result = self.run_terminal(command, timeout=timeout)
        status = "ok" if result["returncode"] == 0 else "error"
        return json.dumps({"status": status, "result": result}, ensure_ascii=False)

    def _manage_plan_tool(self, tool_input: Dict[str, Any]) -> str:
        action = str(tool_input.get("action", "list")).strip().lower() or "list"
        allowed_statuses = {"pending", "in_progress", "completed"}

        if action == "clear":
            self.plan = []
        elif action == "set":
            raw_tasks = tool_input.get("tasks") or []
            if not isinstance(raw_tasks, list):
                return json.dumps({"status": "error", "error": "tasks must be a list"})

            new_plan: List[Dict[str, str]] = []
            in_progress_count = 0
            for raw_task in raw_tasks:
                if not isinstance(raw_task, dict):
                    return json.dumps({"status": "error", "error": "each task must be an object"})
                step = str(raw_task.get("step", "")).strip()
                task_status = str(raw_task.get("status", "pending")).strip()
                if not step:
                    return json.dumps({"status": "error", "error": "task step cannot be empty"})
                if task_status not in allowed_statuses:
                    return json.dumps({"status": "error", "error": f"Invalid task status: {task_status}"})
                if task_status == "in_progress":
                    in_progress_count += 1
                new_plan.append({"step": step, "status": task_status})
            if in_progress_count > 1:
                return json.dumps({"status": "error", "error": "Only one task can be in_progress"})
            self.plan = new_plan
        elif action == "update":
            try:
                index = int(tool_input.get("index"))
            except (TypeError, ValueError):
                return json.dumps({"status": "error", "error": "Missing or invalid index"})
            if index < 0 or index >= len(self.plan):
                return json.dumps({"status": "error", "error": "Task index out of range"})

            step = tool_input.get("step")
            task_status = tool_input.get("status")
            if step is not None:
                cleaned_step = str(step).strip()
                if not cleaned_step:
                    return json.dumps({"status": "error", "error": "task step cannot be empty"})
                self.plan[index]["step"] = cleaned_step
            if task_status is not None:
                cleaned_status = str(task_status).strip()
                if cleaned_status not in allowed_statuses:
                    return json.dumps({"status": "error", "error": f"Invalid task status: {cleaned_status}"})
                if cleaned_status == "in_progress":
                    for task_index, task in enumerate(self.plan):
                        if task_index != index and task["status"] == "in_progress":
                            task["status"] = "pending"
                self.plan[index]["status"] = cleaned_status
        elif action != "list":
            return json.dumps({"status": "error", "error": f"Unknown plan action: {action}"})

        return json.dumps({"status": "ok", "plan": self.plan}, ensure_ascii=False)

    @staticmethod
    def docker_language_spec(language: str) -> Dict[str, Any]:
        specs = {
            "python": {
                "image": "python:3.12-slim",
                "filename": "main.py",
                "build": None,
                "command": ["python", "/sandbox/main.py"],
                "type": "interpreted",
            },
            "javascript": {
                "image": "node:22-slim",
                "filename": "main.js",
                "build": None,
                "command": ["node", "/sandbox/main.js"],
                "type": "interpreted",
            },
            "shell": {
                "image": "alpine:3.20",
                "filename": "main.sh",
                "build": "chmod +x /sandbox/main.sh",
                "command": ["/bin/sh", "/sandbox/main.sh"],
                "type": "shell",
            },
            "typescript" : {
                "image": "node:22-silm",
                "filename": "main.ts",
                "build": "npm install -g ts-node typescript",
                "run": "ts-node /sandbox/main.sh",
                "type": "interpreted",
            },
            "tsx": {
                "image": "node:22-slim",
                "filename": "main.tsx",
                "build": (
                    "npm install -g tsx typescript "
                    "react react-dom"
                ),
                "run": "tsx /sandbox/main.tsx",
                "type": "frontend",
            },
            "html": {
                "image": "mcr.microsoft.com/playwright:v1.44.0-jammy",
                "filename": "index.html",
                "build": None,
                "run": (
                    "python3 -m http.server 8000 --directory /sandbox "
                    "& sleep 2 "
                    "&& node /browser/render.js"
                ),
                "type": "browser",
            },
            "css": {
                "image": "mcr.microsoft.com/playwright:v1.44.0-jammy",
                "filename": "style.css",
                "build": None,
                "run": (
                    "python3 -m http.server 8000 --directory /sandbox "
                    "& sleep 2 "
                    "&& node /browser/render.js"
                ),
                "type": "browser",
            },
            "json": {
                "image": "python:3.12-slim",
                "filename": "data.json",
                "build": None,
                "run": "python -m json.tool /sandbox/data.json",
                "type": "data",
            },
            "yaml": {
                "image": "python:3.12-slim",
                "filename": "data.yaml",
                "build": "pip install pyyaml",
                "run": (
                    "python -c \"import yaml; "
                    "print(yaml.safe_load(open('/sandbox/data.yaml')))\""
                ),
                "type": "data",
            },
            "toml": {
                "image": "python:3.12-slim",
                "filename": "data.toml",
                "build": None,
                "run": (
                    "python -c \"import tomllib; "
                    "print(tomllib.load(open('/sandbox/data.toml', 'rb')))\""
                ),
                "type": "data",
            },
            "xml": {
                "image": "python:3.12-slim",
                "filename": "data.xml",
                "build": None,
                "run": (
                    "python -c \"import xml.dom.minidom as md; "
                    "print(md.parse('/sandbox/data.xml').toprettyxml())\""
                ),
                "type": "data",
            },
            "bash": {
                "image": "bash:latest",
                "filename": "main.sh",
                "build": "chmod +x /sandbox/main.sh",
                "run": "/sandbox/main.sh",
                "type": "shell",
            },
            "c": {
                "image": "gcc:latest",
                "filename": "main.c",
                "build": "gcc /sandbox/main.c -o /sandbox/a.out",
                "run": "/sandbox/a.out",
                "type": "compiled",
            },
            "cpp": {
                "image": "gcc:latest",
                "filename": "main.cpp",
                "build": "g++ /sandbox/main.cpp -o /sandbox/a.out",
                "run": "/sandbox/a.out",
                "type": "compiled",
            },
            "csharp": {
                "image": "mcr.microsoft.com/dotnet/sdk:8.0",
                "filename": "Program.cs",
                "build": (
                    "cd /sandbox && "
                    "dotnet new console --force && "
                    "mv Program.cs ./Program.cs"
                ),
                "run": "cd /sandbox && dotnet run",
                "type": "compiled",
            },
            "java": {
                "image": "eclipse-temurin:21",
                "filename": "Main.java",
                "build": "javac /sandbox/Main.java",
                "run": "java -cp /sandbox Main",
                "type": "compiled",
            },
            "kotlin": {
                "image": "gradle:8.7-jdk21",
                "filename": "main.kt",
                "build": (
                    "kotlinc /sandbox/main.kt "
                    "-include-runtime "
                    "-d /sandbox/app.jar"
                ),
                "run": "java -jar /sandbox/app.jar",
                "type": "compiled",
            },
            "go": {
                "image": "golang:1.22",
                "filename": "main.go",
                "build": None,
                "run": "go run /sandbox/main.go",
                "type": "compiled",
            },
            "rust": {
                "image": "rust:latest",
                "filename": "main.rs",
                "build": "rustc /sandbox/main.rs -o /sandbox/app",
                "run": "/sandbox/app",
                "type": "compiled",
            },
            "ruby": {
                "image": "ruby3.3",
                "filename": "main.rb",
                "build": None,
                "run": "ruby /sandbox/main.rb",
                "type": "interpreted",
            },
            "php": {
                "image": "php:8-cli",
                "filename": "main.php",
                "build": None,
                "run": "ruby /sandbox/main.php",
                "type": "interpreted",
            },
            "lua": {
                "image": "lua:5.4",
                "filename": "main.lua",
                "build": None,
                "run": "lua /sandbox/main.lua",
                "type": "interpreted",
            },
            "r": {
                "image": "r-base",
                "filename": "main.R",
                "build": None,
                "run": "Rscript /sandbox/main.R",
                "type": "interpreted",
            },
            "swift": {
                "image": "swift:latest",
                "filename": "main.swift",
                "build": None,
                "run": "swift /sandbox/main.swift",
                "type": "compiled",
            },
            "dart": {
                "image": "dart:stable",
                "filename": "main.dart",
                "build": None,
                "run": "dart /sandbox/main.dart",
                "type": "compiled",
            },
            "scala": {
                "image": "hseeberger/scala-sbt",
                "filename": "main.scala",
                "build": None,
                "run": "scala /sandbox/main.scala",
                "type": "compiled",
            },
            "haskell": {
                "image": "haskell:latest",
                "filename": "main.hs",
                "build": None,
                "run": "runghc /sandbox/main.hs",
                "type": "compiled",
            },
            "perl": {
                "image": "perl:latest",
                "file_name": "main.pl",
                "build": None,
                "run": "perl /sandbox/main.pl",
                "type": "interpreted",
            },
            "elixir": {
                "image": "elixir:latest",
                "filename": "main.exs",
                "build": None,
                "run": "elixir /sandbox/main.exs",
                "type": "interpreted",
            },
            "clojure": {
                "image": "clojure:tools-deps",
                "filename": "main.clj",
                "build": None,
                "run": "clojure /sandbox/main.clj",
                "type": "interpreted",
            },
        }
        if language not in specs:
            supported = ", ".join(sorted(specs))
            raise ValueError(f"Unsupported language '{language}'. Supported: {supported}")
        return specs[language]

    def _screenshot_from_container(self, host_path: Path, timeout: int) -> Optional[bytes]:
        """Run Playwright inside the sandbox container to screenshot the rendered HTML."""
        screenshot_script = """
    import asyncio
    from playwright.async_api import async_playwright

    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.goto("http://localhost:8000", wait_until="networkidle", timeout=10000)
            await page.screenshot(path="/sandbox/screenshot.png", full_page=True)
            await browser.close()

    asyncio.run(main())
    """.strip()
        script_path = host_path / "capture.py"
        script_path.write_text(screenshot_script, encoding="utf-8")

        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", "host",  # needs localhost access
                "--memory", "512m",
                "-v", f"{host_path}:/sandbox",
                "-w", "/sandbox",
                "mcr.microsoft.com/playwright:v1.44.0-jammy",
                "sh", "-c",
                "python3 -m http.server 8000 --directory /sandbox &"
                " sleep 2 && python3 /sandbox/capture.py",
            ],
            capture_output=True,
            timeout=timeout + 15,
        )
        screenshot_path = host_path / "screenshot.png"
        if result.returncode == 0 and screenshot_path.exists():
            return screenshot_path.read_bytes()
        return None

    def _analyze_screenshot(self, image_bytes: bytes, language: str) -> str:
        """Feed screenshot to a vision model and get a structured UI description."""
        import base64
        vision_model = "translategemma:4b"

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = (
            f"You are analyzing a rendered {language.upper()} page screenshot."
            "And you are an expert image analyzer you make no mistakes and you always explain exactly what you see"
            "Describe it in this structured format:\n\n"
            "LAYOUT: <overall page structure>\n"
            "ELEMENTS: <list of visible UI elements>\n"
            "STYLES: <colors, fonts, spacing observations>\n"
            "ISSUES: <anything broken, overflowing, misaligned, or missing>\n"
            "SUGGESTIONS: <concrete improvements>\n\n"
            "Be precise and developer-focused."
            "And at all cost never hallucinate with what you see explain with full precision"
        )
        try:
            response = ollama.chat(
                model=vision_model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [b64],
                }],
                stream=False,
            )
            return response.get("message", {}).get("content", "Screenshot analysis unavailable.")
        except Exception as exc:
            return f"Vision model error: {exc}"
    def execute_code_in_docker(self, code: str, language: str = "python", timeout: int = 10) -> Dict[str, Any]:
        if not code.strip():
            return {"error": "Missing code", "returncode": 2}
        if len(code) > 100_000_00:
            return {"error": "Code is too large; maximum size is 10,000,000 characters.", "returncode": 2}

        spec = self.docker_language_spec(language)
        timeout = min(max(1, timeout), 60)

        with tempfile.TemporaryDirectory(prefix="claw-coder-sandbox-") as temp_dir:
            host_path = Path(temp_dir)
            code_path = host_path / spec["filename"]
            code_path.write_text(code, encoding="utf-8")
            if spec.get("type") in ("browser",):
                screenshot_bytes = self._screenshot_from_container(host_path, timeout)
                if screenshot_bytes:
                    analysis = self._analyze_screenshot(screenshot_bytes, language)
                    return {
                        "language": language,
                        "image": spec["image"],
                        "stdout": "",
                        "stderr": "",
                        "returncode": 0,
                        "timeout": timeout,
                        "ui_analysis": analysis,  # ← injected into tool result
                        "screenshot_captured": True,
                    }
                else:
                    return {
                        "language": language,
                        "image": spec["image"],
                        "stdout": "",
                        "stderr": "Screenshot failed — Playwright may not have rendered the page.",
                        "returncode": 1,
                        "timeout": timeout,
                        "screenshot_captured": False,
                    }
            docker_command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "--pids-limit",
                "128",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "-v",
                f"{host_path}:/sandbox:ro",
                "-w",
                "/sandbox",
                spec["image"],
                *spec["command"],
            ]
            try:
                result = subprocess.run(
                    docker_command,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return {
                    "language": language,
                    "image": spec["image"],
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                    "timeout": timeout,
                }
            except FileNotFoundError:
                return {
                    "language": language,
                    "image": spec["image"],
                    "stdout": "",
                    "stderr": "Docker executable not found. Install Docker and ensure it is on PATH.",
                    "returncode": 127,
                    "timeout": timeout,
                }
            except subprocess.TimeoutExpired as exc:
                return {
                    "language": language,
                    "image": spec["image"],
                    "stdout": self.decode_process_output(exc.stdout),
                    "stderr": f"Docker sandbox timed out after {timeout} seconds.",
                    "returncode": 124,
                    "timeout": timeout,
                }

    def _execute_code_in_docker_tool(self, tool_input: Dict[str, Any]) -> str:
        code = str(tool_input.get("code", ""))
        language = str(tool_input.get("language", "python")).strip().lower() or "python"
        timeout = int(tool_input.get("timeout", 10))
        result = self.execute_code_in_docker(code=code, language=language, timeout=timeout)
        status = "ok" if result.get("returncode") == 0 else "error"
        return json.dumps({"status": status, "result": result}, ensure_ascii=False)

    def _manage_memory_tool(self, tool_input: Dict[str, Any]) -> str:
        action = str(tool_input.get("action", "list")).strip().lower() or "list"
        limit = min(max(1, int(tool_input.get("limit", 10))), 50)

        if action == "add":
            content = str(tool_input.get("content", "")).strip()
            if not content:
                return json.dumps({"status": "error", "error": "Missing content"})
            kind = str(tool_input.get("kind", "note")).strip() or "note"
            entry = self.add_memory(kind=kind, content=content)
            return json.dumps({"status": "ok", "memory": entry}, ensure_ascii=False)

        if action == "search":
            query = str(tool_input.get("query", "")).strip().lower()
            if not query:
                return json.dumps({"status": "error", "error": "Missing query"})
            matches = [
                entry for entry in self.memory
                if query in str(entry.get("content", "")).lower()
                or query in str(entry.get("kind", "")).lower()
            ]
            return json.dumps({"status": "ok", "memories": matches[-limit:]}, ensure_ascii=False)

        if action == "clear":
            self.memory = []
            self.save_memory()
            return json.dumps({"status": "ok", "memories": []})

        if action != "list":
            return json.dumps({"status": "error", "error": f"Unknown memory action: {action}"})
        return json.dumps({"status": "ok", "memories": self.memory[-limit:]}, ensure_ascii=False)

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        tool_events: List[Dict[str, Any]] = []
        for _ in range(self.max_steps):
            response = ollama.chat(
                model=self.model,
                messages=self.messages,
                tools=self.tools,
                stream=False,
            )
            message = response.get("message", {})
            assistant_message = {"role": "assistant", "content": message.get("content", "")}
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            self.messages.append(assistant_message)

            if not tool_calls:
                final_message = message.get("content", "")
                self.add_memory(
                    "interaction",
                    f"User: {user_input}\nAgent: {final_message}",
                    metadata={"tool_events": tool_events},
                )
                return final_message

            for call in tool_calls:
                function_data = call.get("function", {})
                tool_name = function_data.get("name", "")
                tool_args = self.parse_tool_arguments(function_data.get("arguments", {}))
                result = self.execute_tool(tool_name, tool_args)
                try:
                    result_data = json.loads(result)
                    tool_status = result_data.get("status", "unknown")
                except json.JSONDecodeError:
                    tool_status = "unknown"
                tool_events.append({"tool": tool_name, "status": tool_status})
                self.messages.append({"role": "tool", "content": result})

        final_message = "I reached the tool-execution step limit before finishing."
        self.add_memory(
            "interaction",
            f"User: {user_input}\nAgent: {final_message}",
            metadata={"tool_events": tool_events},
        )
        return final_message


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def preview_code_chunks(path: str, language: Optional[str] = None) -> None:
    chunks = tree_sitter_code_chunks(path, language=language)
    print_json(
        [
            {
                "source": chunk.metadata.get("source"),
                "language": chunk.metadata.get("language"),
                "symbol_type": chunk.metadata.get("symbol_type"),
                "symbol_name": chunk.metadata.get("symbol_name"),
                "start_point": chunk.metadata.get("start_point"),
                "end_point": chunk.metadata.get("end_point"),
                "text": Agent.trim_text(chunk.page_content, 500),
            }
            for chunk in chunks
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone RAG agent")
    parser.add_argument("--model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--knowledge-graph-path", default=DEFAULT_KNOWLEDGE_GRAPH_PATH)
    parser.add_argument("--memory-path", default=DEFAULT_MEMORY_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("chat")
    subparsers.add_parser("languages")

    code_chunks = subparsers.add_parser("code-chunks")
    code_chunks.add_argument("path")
    code_chunks.add_argument("--language")

    ingest_code = subparsers.add_parser("ingest-code")
    ingest_code.add_argument("path")
    ingest_code.add_argument("--language")

    ingest_paths = subparsers.add_parser("ingest-paths")
    ingest_paths.add_argument("paths", nargs="+")
    ingest_paths.add_argument("--no-recursive", action="store_true")
    ingest_paths.add_argument("--no-vector-rag", action="store_true")

    ingest_pdf = subparsers.add_parser("ingest-pdf")
    ingest_pdf.add_argument("path", nargs="?", default=str(DEFAULT_PDF))

    search_kb = subparsers.add_parser("search-kb")
    search_kb.add_argument("query")
    search_kb.add_argument("--top-k", type=int, default=4)
    search_kb.add_argument("--no-hybrid-rerank", action="store_true")

    search_graph = subparsers.add_parser("search-graph")
    search_graph.add_argument("query")
    search_graph.add_argument("--top-k", type=int, default=8)
    search_graph.add_argument("--depth", type=int, default=2)

    subparsers.add_parser("graph-summary")
    memory_summary = subparsers.add_parser("memory-summary")
    memory_summary.add_argument("--limit", type=int, default=20)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "languages":
        print_json({"rag_tree_sitter": available_languages(), "graph_tree_sitter": graph_tree_sitter_languages()})
        return

    if args.command == "code-chunks":
        preview_code_chunks(args.path, language=args.language)
        return
    model = args.model or input("Enter model name (e.g. mistral, llama3.2:3b): ").strip()
    if not model:
        print("Error: --model is required. Example: claw mistral")
        return
    agent = Agent(
        model=model,
        embedding_model=args.embedding_model,
        rag_db_path=args.db_path,
        rag_collection=args.collection,
        knowledge_graph_path=args.knowledge_graph_path,
        memory_path=args.memory_path,
    )

    if args.command == "ingest-code":
        print(agent.execute_tool("ingest_code_knowledge", {"path": args.path, "language": args.language}))
        return
    if args.command == "ingest-paths":
        print(
            agent.execute_tool(
                "ingest_paths_knowledge",
                {
                    "paths": args.paths,
                    "recursive": not args.no_recursive,
                    "ingest_vector_rag": not args.no_vector_rag,
                },
            )
        )
        return
    if args.command == "ingest-pdf":
        print(agent.execute_tool("ingest_pdf_knowledge", {"path": args.path}))
        return
    if args.command == "search-kb":
        print(
            agent.execute_tool(
                "search_knowledge_base",
                {
                    "query": args.query,
                    "top_k": args.top_k,
                    "hybrid_rerank": not args.no_hybrid_rerank,
                },
            )
        )
        return
    if args.command == "search-graph":
        print(
            agent.execute_tool(
                "search_knowledge_graph",
                {"query": args.query, "top_k": args.top_k, "depth": args.depth},
            )
        )
        return
    if args.command == "graph-summary":
        print_json(agent.knowledge_graph().summary())
        return
    if args.command == "memory-summary":
        print(agent.execute_tool("manage_memory", {"action": "list", "limit": args.limit}))
        return
    if args.command == "chat":
        print("""
|===================================================|
|   |=============|                                 |
|   |Claw-Coder ✌️|                                 |
|   |=============|                                 |
|   Type = {                                        |
|       'exit': 'quit'                              |
|   }   <- Say bye to claw                          |
|                                                   |                                 
|===================================================|
        """)
        while True:
            user_input = input("Type anything to interact with Claw-Coder: ")
            print("========================================================")
            if user_input.lower() in {"exit", "quit"}:
                break
            print(f"\nClaw-Coder: {agent.chat(user_input)}\n")
            print("=========================================================")


if __name__ == "__main__":
    main()
