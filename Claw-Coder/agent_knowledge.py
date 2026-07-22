"""
Local multi-file knowledge graph for Claw Coder.

The graph uses Tree-sitter for supported languages, Python AST as a fallback for
Python files, regular expressions for remaining text files, and JSON persistence.
It complements agent_rag.py's vector store by keeping explicit relationships
between files, symbols, imports, calls, chunks, and extracted entities.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_GRAPH_PATH = "agent_knowledge_graph.json"
DEFAULT_MAX_FILE_BYTES = 1_000_000
DEFAULT_CHUNK_CHARS = 1600
DEFAULT_CHUNK_OVERLAP = 250

RELATION_WEIGHTS = {
    "defines": 3.0,
    "contains": 2.5,
    "calls": 2.25,
    "imports": 1.75,
    "mentions": 1.0,
    "next_chunk": 0.35,
    "shares_entity": 0.75,
    "same_directory": 0.5,
}

SUPPORTED_TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".md",
    ".txt",
    ".csv",
    ".html",
    ".htm",
    ".css",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".r",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".yaml",
    ".yml",
    ".toml",
}

LANGUAGE_SPECS: Dict[str, Dict[str, str]] = {
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
    "css": {"module": "tree_sitter_css", "function": "language"}
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
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}

TREE_SITTER_SYMBOL_NODE_TYPES = {
    "function_definition": "function",
    "function_declaration": "function",
    "method_definition": "method",
    "method_declaration": "method",
    "class_definition": "class",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "lexical_declaration": "variable",
    "variable_declaration": "variable",
    "const_declaration": "variable",
    "function_item": "function",
    "struct_item": "struct",
    "enum_item": "enum",
    "impl_item": "impl",
    "source_file": "file",
    "program": "file",
}

TREE_SITTER_IMPORT_NODE_TYPES = {
    "import_statement",
    "import_declaration",
    "import_from_statement",
    "use_declaration",
}

TREE_SITTER_CALL_NODE_TYPES = {
    "call",
    "call_expression",
    "method_invocation",
}

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    ".idea",
    ".vscode",
    "agent_rag_chroma_db",
}

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "into",
    "your",
    "you",
    "are",
    "was",
    "were",
    "has",
    "have",
    "not",
    "but",
    "can",
    "will",
    "all",
    "any",
    "use",
    "using",
    "def",
    "class",
    "return",
    "import",
}


@dataclass(slots=True)
class GraphNode:
    id: str
    kind: str
    name: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphEdge:
    source: str
    target: str
    relation: str
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphSearchResult:
    node: GraphNode
    score: float
    edges: List[GraphEdge]


def stable_node_id(kind: str, name: str, scope: str = "") -> str:
    digest = hashlib.sha256(f"{kind}:{scope}:{name}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:20]}"


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def token_list(value: str) -> List[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value)
        if token.lower() not in STOPWORDS
    ]


def tokenize(value: str) -> Set[str]:
    return set(token_list(value))


def looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(2048)
    except OSError:
        return True
    return b"\0" in sample


def infer_language(path: Path) -> Optional[str]:
    return EXTENSION_TO_LANGUAGE.get(path.suffix.lower())


def require_tree_sitter():
    try:
        from tree_sitter import Language, Parser
    except ImportError as exc:
        raise RuntimeError("tree-sitter is missing. Install it with: pip install tree-sitter") from exc
    return Language, Parser


def load_tree_sitter_parser(language_name: str):
    Language, Parser = require_tree_sitter()
    spec = LANGUAGE_SPECS.get(language_name)
    if not spec:
        raise RuntimeError(f"Unsupported Tree-sitter language: {language_name}")

    module = importlib.import_module(spec["module"])
    language_fn = getattr(module, spec["function"])
    parser = Parser()
    parser.language = Language(language_fn())
    return parser


def tree_sitter_available_languages() -> Dict[str, Dict[str, Any]]:
    status: Dict[str, Dict[str, Any]] = {}
    for language, spec in LANGUAGE_SPECS.items():
        try:
            module = importlib.import_module(spec["module"])
            getattr(module, spec["function"])
            status[language] = {"available": True, "module": spec["module"], "install": None}
        except Exception:
            status[language] = {
                "available": False,
                "module": spec["module"],
                "install": f"pip install {spec['module'].replace('_', '-')}",
            }
    return status


def ts_node_text(source: bytes, node: Any) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def child_name_for_symbol(source: bytes, node: Any) -> str:
    for field in ("name", "declarator"):
        child = node.child_by_field_name(field)
        if child is not None:
            text = ts_node_text(source, child).strip()
            match = re.search(r"[A-Za-z_][A-Za-z0-9_]*", text)
            if match:
                return match.group(0)

    for child in node.children:
        if child.type in {"identifier", "property_identifier", "type_identifier", "field_identifier"}:
            return ts_node_text(source, child).strip()
    return f"{node.type}_{node.start_point[0] + 1}"


def walk_tree_sitter(node: Any) -> Iterable[Any]:
    yield node
    for child in node.children:
        yield from walk_tree_sitter(child)


def iter_supported_files(
    paths: Sequence[str | Path],
    recursive: bool = True,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> List[Path]:
    files: List[Path] = []
    seen: Set[Path] = set()

    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            continue

        candidates: Iterable[Path]
        if path.is_dir():
            walker = path.rglob("*") if recursive else path.glob("*")
            candidates = (candidate for candidate in walker if candidate.is_file())
        else:
            candidates = [path]

        for candidate in candidates:
            if any(part in IGNORED_DIRS for part in candidate.parts):
                continue
            if candidate in seen:
                continue
            if candidate.suffix.lower() not in SUPPORTED_TEXT_EXTENSIONS:
                continue
            try:
                if candidate.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            if looks_binary(candidate):
                continue
            seen.add(candidate)
            files.append(candidate)

    return sorted(files)


class KnowledgeGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: Dict[Tuple[str, str, str], GraphEdge] = {}
        self._tokens: Dict[str, Set[str]] = {}
        self._centrality: Optional[Dict[str, float]] = None

    def add_node(
        self,
        kind: str,
        name: str,
        metadata: Optional[Dict[str, Any]] = None,
        node_id: Optional[str] = None,
        scope: str = "",
    ) -> GraphNode:
        final_id = node_id or stable_node_id(kind, name, scope=scope)
        incoming = metadata or {}
        if final_id in self.nodes:
            self.nodes[final_id].metadata.update(incoming)
            return self.nodes[final_id]

        node = GraphNode(id=final_id, kind=kind, name=name, metadata=dict(incoming))
        self.nodes[final_id] = node
        self._tokens[final_id] = tokenize(" ".join([node.kind, node.name, json.dumps(node.metadata, default=str)]))
        self._centrality = None
        return node

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if source == target:
            return
        key = (source, target, relation)
        if key in self.edges:
            self.edges[key].weight += weight
            self.edges[key].metadata.update(metadata or {})
            self._centrality = None
            return
        self.edges[key] = GraphEdge(
            source=source,
            target=target,
            relation=relation,
            weight=weight,
            metadata=dict(metadata or {}),
        )
        self._centrality = None

    def merge(self, other: "KnowledgeGraph") -> None:
        for node in other.nodes.values():
            self.add_node(node.kind, node.name, node.metadata, node_id=node.id)
        for edge in other.edges.values():
            self.add_edge(edge.source, edge.target, edge.relation, edge.weight, edge.metadata)

    def neighbors(self, node_id: str, limit: int = 12) -> List[GraphEdge]:
        related = [
            edge
            for edge in self.edges.values()
            if edge.source == node_id or edge.target == node_id
        ]
        return sorted(related, key=lambda edge: edge.weight, reverse=True)[:limit]

    def outgoing(self, node_id: str) -> List[GraphEdge]:
        return [edge for edge in self.edges.values() if edge.source == node_id]

    def incoming(self, node_id: str) -> List[GraphEdge]:
        return [edge for edge in self.edges.values() if edge.target == node_id]

    def centrality_scores(self, iterations: int = 20, damping: float = 0.85) -> Dict[str, float]:
        if self._centrality is not None:
            return self._centrality
        if not self.nodes:
            self._centrality = {}
            return self._centrality

        scores = {node_id: 1.0 / len(self.nodes) for node_id in self.nodes}
        outgoing = {node_id: self.outgoing(node_id) for node_id in self.nodes}
        for _ in range(iterations):
            next_scores = {node_id: (1.0 - damping) / len(self.nodes) for node_id in self.nodes}
            for node_id, edges in outgoing.items():
                if not edges:
                    continue
                total_weight = sum(max(edge.weight, 0.1) for edge in edges)
                for edge in edges:
                    relation_bias = RELATION_WEIGHTS.get(edge.relation, 1.0)
                    contribution = scores[node_id] * damping * (max(edge.weight, 0.1) / total_weight)
                    next_scores[edge.target] = next_scores.get(edge.target, 0.0) + contribution * relation_bias
            scores = next_scores

        max_score = max(scores.values()) if scores else 1.0
        self._centrality = {node_id: score / max_score for node_id, score in scores.items()}
        return self._centrality

    def shortest_paths(self, start: str, target: str, max_depth: int = 3, limit: int = 3) -> List[List[GraphEdge]]:
        if start not in self.nodes or target not in self.nodes:
            return []

        paths: List[List[GraphEdge]] = []
        queue: deque[Tuple[str, List[GraphEdge]]] = deque([(start, [])])
        visited_depth: Dict[str, int] = {start: 0}

        while queue and len(paths) < limit:
            node_id, path = queue.popleft()
            if len(path) >= max_depth:
                continue
            for edge in self.neighbors(node_id, limit=30):
                next_node = edge.target if edge.source == node_id else edge.source
                next_path = path + [edge]
                if next_node == target:
                    paths.append(next_path)
                    continue
                if visited_depth.get(next_node, max_depth + 1) <= len(next_path):
                    continue
                visited_depth[next_node] = len(next_path)
                queue.append((next_node, next_path))

        return paths

    def search(self, query: str, top_k: int = 8) -> List[GraphSearchResult]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        centrality = self.centrality_scores()
        scored: List[Tuple[float, GraphNode]] = []
        query_lower = query.lower()
        for node_id, node in self.nodes.items():
            node_tokens = self._tokens.get(node_id) or tokenize(node.name)
            overlap = len(query_tokens & node_tokens)
            if overlap == 0 and query_lower not in node.name.lower():
                continue

            relation_boost = sum(
                RELATION_WEIGHTS.get(edge.relation, 1.0) * min(edge.weight, 5.0)
                for edge in self.neighbors(node_id, limit=10)
            ) / 10.0
            score = float(overlap) + relation_boost + centrality.get(node_id, 0.0)
            if query_lower in node.name.lower():
                score += 3.0
            if node.kind in {"file", "symbol"}:
                score += 0.5
            scored.append((score, node))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            GraphSearchResult(node=node, score=score, edges=self.neighbors(node.id))
            for score, node in scored[: max(1, top_k)]
        ]

    def related_subgraph(self, query: str, depth: int = 2, limit: int = 40) -> Dict[str, Any]:
        roots = self.search(query, top_k=5)
        visited: Set[str] = set()
        selected_edges: List[GraphEdge] = []
        selected_edge_keys: Set[Tuple[str, str, str]] = set()
        queue: deque[Tuple[str, int]] = deque((result.node.id, 0) for result in roots)

        while queue and len(visited) < limit:
            node_id, current_depth = queue.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            if current_depth >= depth:
                continue
            for edge in self.neighbors(node_id):
                edge_key = (edge.source, edge.target, edge.relation)
                if edge_key not in selected_edge_keys and len(selected_edges) < limit * 2:
                    selected_edge_keys.add(edge_key)
                    selected_edges.append(edge)
                other = edge.target if edge.source == node_id else edge.source
                if other not in visited:
                    queue.append((other, current_depth + 1))

        return {
            "nodes": [asdict(self.nodes[node_id]) for node_id in visited if node_id in self.nodes],
            "edges": [asdict(edge) for edge in selected_edges],
        }

    def explain_result(self, node_id: str, query: str) -> Dict[str, Any]:
        node = self.nodes[node_id]
        query_tokens = tokenize(query)
        node_tokens = self._tokens.get(node_id) or tokenize(node.name)
        centrality = self.centrality_scores().get(node_id, 0.0)
        top_edges = self.neighbors(node_id, limit=8)
        return {
            "matched_terms": sorted(query_tokens & node_tokens),
            "centrality": centrality,
            "top_relations": [
                {
                    "relation": edge.relation,
                    "weight": edge.weight,
                    "other": asdict(self.nodes[edge.target if edge.source == node_id else edge.source]),
                }
                for edge in top_edges
                if (edge.target if edge.source == node_id else edge.source) in self.nodes
            ],
            "node": asdict(node),
        }

    def summary(self) -> Dict[str, Any]:
        node_counts = Counter(node.kind for node in self.nodes.values())
        edge_counts = Counter(edge.relation for edge in self.edges.values())
        return {
            "nodes": sum(node_counts.values()),
            "edges": sum(edge_counts.values()),
            "node_kinds": dict(sorted(node_counts.items())),
            "edge_relations": dict(sorted(edge_counts.items())),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [asdict(node) for node in self.nodes.values()],
            "edges": [asdict(edge) for edge in self.edges.values()],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "KnowledgeGraph":
        graph = cls()
        for raw_node in payload.get("nodes", []):
            graph.add_node(
                raw_node["kind"],
                raw_node["name"],
                raw_node.get("metadata") or {},
                node_id=raw_node["id"],
            )
        for raw_edge in payload.get("edges", []):
            graph.add_edge(
                raw_edge["source"],
                raw_edge["target"],
                raw_edge["relation"],
                float(raw_edge.get("weight", 1.0)),
                raw_edge.get("metadata") or {},
            )
        return graph


class KnowledgeGraphBuilder:
    def build_from_paths(
        self,
        paths: Sequence[str | Path],
        recursive: bool = True,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> Tuple[KnowledgeGraph, List[Path]]:
        graph = KnowledgeGraph()
        files = iter_supported_files(paths, recursive=recursive, max_file_bytes=max_file_bytes)
        for path in files:
            self.add_file(graph, path)
        self.add_cross_file_edges(graph)
        return graph, files

    def add_file(self, graph: KnowledgeGraph, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        language = infer_language(path)
        file_node = graph.add_node(
            "file",
            path.name,
            {
                "path": str(path),
                "extension": path.suffix.lower(),
                "language": language,
                "size": path.stat().st_size,
                "sha256": content_hash,
                "tokens": sorted(list(tokenize(text)))[:200],
            },
            node_id=stable_node_id("file", str(path)),
        )
        directory_node = graph.add_node("directory", path.parent.name or str(path.parent), {"path": str(path.parent)})
        graph.add_edge(directory_node.id, file_node.id, "contains")
        self.add_text_chunks(graph, file_node, path, text)

        if language and self.add_tree_sitter_structure(graph, file_node, path, text, language):
            file_node.metadata["parser"] = "tree-sitter"
        elif path.suffix.lower() == ".py":
            file_node.metadata["parser"] = "python-ast"
            self.add_python_structure(graph, file_node, path, text)
        else:
            file_node.metadata["parser"] = "regex"
            self.add_generic_structure(graph, file_node, path, text)

    def add_tree_sitter_structure(
        self,
        graph: KnowledgeGraph,
        file_node: GraphNode,
        path: Path,
        text: str,
        language: str,
    ) -> bool:
        try:
            parser = load_tree_sitter_parser(language)
        except Exception:
            return False

        source = text.encode("utf-8")
        tree = parser.parse(source)
        symbol_by_range: Dict[Tuple[int, int], GraphNode] = {}
        symbols_by_name: Dict[str, GraphNode] = {}

        for node in walk_tree_sitter(tree.root_node):
            symbol_kind = TREE_SITTER_SYMBOL_NODE_TYPES.get(node.type)
            if not symbol_kind or symbol_kind == "file":
                continue
            name = child_name_for_symbol(source, node)
            content = ts_node_text(source, node)
            symbol = graph.add_node(
                "symbol",
                name,
                {
                    "path": str(path),
                    "language": language,
                    "symbol_kind": symbol_kind,
                    "node_type": node.type,
                    "start_byte": node.start_byte,
                    "end_byte": node.end_byte,
                    "line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "preview": normalize_text(content)[:300],
                    "has_error": bool(tree.root_node.has_error),
                },
                scope=str(path),
            )
            symbol_by_range[(node.start_byte, node.end_byte)] = symbol
            symbols_by_name[name] = symbol
            graph.add_edge(file_node.id, symbol.id, "defines", weight=2.5, metadata={"line": node.start_point[0] + 1})

        for node in walk_tree_sitter(tree.root_node):
            if node.type in TREE_SITTER_IMPORT_NODE_TYPES:
                raw_import = normalize_text(ts_node_text(source, node))
                for module_name in self.extract_import_names(raw_import):
                    module = graph.add_node("module", module_name)
                    graph.add_edge(file_node.id, module.id, "imports", weight=1.5, metadata={"raw": raw_import[:300]})

            if node.type in TREE_SITTER_CALL_NODE_TYPES:
                call_name = self.extract_call_name(source, node)
                if not call_name:
                    continue
                owner = self.find_tree_sitter_owner(node, symbol_by_range)
                target = symbols_by_name.get(call_name) or graph.add_node("entity", call_name)
                if owner:
                    graph.add_edge(owner.id, target.id, "calls", weight=1.5, metadata={"line": node.start_point[0] + 1})
                else:
                    graph.add_edge(file_node.id, target.id, "mentions", weight=1.0, metadata={"line": node.start_point[0] + 1})

        self.add_entities(graph, file_node, path, text)
        return True

    def extract_import_names(self, raw_import: str) -> Set[str]:
        names: Set[str] = set()
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", raw_import)
        names.update(part.split("/")[0].split(".")[0] for part in quoted if part)
        for match in re.findall(r"\b(?:from|import|use|package)\s+([A-Za-z0-9_./:-]+)", raw_import):
            names.add(match.split("/")[0].split(".")[0].strip(":;"))
        return {name for name in names if name and name not in {".", ".."}}

    def extract_call_name(self, source: bytes, node: Any) -> Optional[str]:
        function_node = node.child_by_field_name("function")
        target = function_node or (node.children[0] if node.children else None)
        if target is None:
            return None
        text = ts_node_text(source, target).strip()
        parts = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
        return parts[-1] if parts else None

    def find_tree_sitter_owner(
        self,
        node: Any,
        symbols_by_range: Dict[Tuple[int, int], GraphNode],
    ) -> Optional[GraphNode]:
        parent = node.parent
        while parent is not None:
            symbol = symbols_by_range.get((parent.start_byte, parent.end_byte))
            if symbol:
                return symbol
            parent = parent.parent
        return None

    def add_text_chunks(self, graph: KnowledgeGraph, file_node: GraphNode, path: Path, text: str) -> None:
        step = max(1, DEFAULT_CHUNK_CHARS - DEFAULT_CHUNK_OVERLAP)
        previous_id: Optional[str] = None
        for index, start in enumerate(range(0, len(text), step)):
            end = min(start + DEFAULT_CHUNK_CHARS, len(text))
            content = normalize_text(text[start:end])
            if not content:
                continue
            chunk = graph.add_node(
                "chunk",
                f"{path.name}#chunk_{index}",
                {
                    "path": str(path),
                    "chunk_index": index,
                    "start": start,
                    "end": end,
                    "preview": content[:300],
                    "tokens": sorted(list(tokenize(content)))[:120],
                },
                scope=str(path),
            )
            graph.add_edge(file_node.id, chunk.id, "contains", weight=1.0)
            if previous_id:
                graph.add_edge(previous_id, chunk.id, "next_chunk", weight=1.0)
            previous_id = chunk.id
            if end >= len(text):
                break

    def add_python_structure(self, graph: KnowledgeGraph, file_node: GraphNode, path: Path, text: str) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            self.add_generic_structure(graph, file_node, path, text)
            return

        imports: Set[str] = set()
        symbols: Dict[str, GraphNode] = {}
        parent_by_child: Dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_by_child[child] = parent

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                symbol = graph.add_node(
                    "symbol",
                    node.name,
                    {
                        "path": str(path),
                        "symbol_kind": kind,
                        "line": getattr(node, "lineno", None),
                        "end_line": getattr(node, "end_lineno", None),
                    },
                    scope=str(path),
                )
                symbols[node.name] = symbol
                graph.add_edge(file_node.id, symbol.id, "defines", weight=2.0, metadata={"line": getattr(node, "lineno", None)})
                parent_symbol = self.find_parent_symbol(node, parent_by_child, symbols)
                if parent_symbol:
                    graph.add_edge(parent_symbol.id, symbol.id, "contains", weight=2.0)

            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])

        for module_name in imports:
            module = graph.add_node("module", module_name)
            graph.add_edge(file_node.id, module.id, "imports")

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            source_symbol = symbols.get(node.name)
            if not source_symbol:
                continue
            calls = self.extract_python_calls(node)
            for call_name in calls:
                target = symbols.get(call_name) or graph.add_node("entity", call_name)
                graph.add_edge(source_symbol.id, target.id, "calls")

        self.add_entities(graph, file_node, path, text)

    def extract_python_calls(self, node: ast.AST) -> Set[str]:
        calls: Set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    calls.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    calls.add(child.func.attr)
        return calls

    def find_parent_symbol(
        self,
        node: ast.AST,
        parent_by_child: Dict[ast.AST, ast.AST],
        symbols: Dict[str, GraphNode],
    ) -> Optional[GraphNode]:
        parent = parent_by_child.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return symbols.get(parent.name)
            parent = parent_by_child.get(parent)
        return None

    def add_generic_structure(self, graph: KnowledgeGraph, file_node: GraphNode, path: Path, text: str) -> None:
        symbol_patterns = [
            r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=",
            r"\b(?:func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)",
        ]
        for pattern in symbol_patterns:
            for match in re.finditer(pattern, text):
                symbol = graph.add_node(
                    "symbol",
                    match.group(1),
                    {"path": str(path), "symbol_kind": "regex", "offset": match.start(1)},
                    scope=str(path),
                )
                graph.add_edge(file_node.id, symbol.id, "defines", weight=2.0)

        for module_name in re.findall(r"\bimport\s+['\"]?([A-Za-z0-9_./-]+)", text):
            module = graph.add_node("module", module_name)
            graph.add_edge(file_node.id, module.id, "imports")

        self.add_entities(graph, file_node, path, text)

    def add_entities(self, graph: KnowledgeGraph, file_node: GraphNode, path: Path, text: str) -> None:
        words = Counter(token_list(text))
        for name, weight in words.most_common(40):
            if len(name) < 4:
                continue
            entity = graph.add_node("entity", name)
            graph.add_edge(file_node.id, entity.id, "mentions", weight=float(weight), metadata={"path": str(path)})

    def add_cross_file_edges(self, graph: KnowledgeGraph) -> None:
        files_by_entity: Dict[str, List[str]] = defaultdict(list)
        for edge in graph.edges.values():
            if edge.relation == "mentions":
                target = graph.nodes.get(edge.target)
                if target and target.kind == "entity":
                    files_by_entity[target.id].append(edge.source)

        for entity_id, file_ids in files_by_entity.items():
            if len(file_ids) < 2:
                continue
            for index, source in enumerate(file_ids):
                for target in file_ids[index + 1 :]:
                    graph.add_edge(source, target, "shares_entity", weight=0.5, metadata={"entity": entity_id})

        files_by_directory: Dict[str, List[str]] = defaultdict(list)
        for node in graph.nodes.values():
            if node.kind == "file":
                directory = str(Path(node.metadata.get("path", "")).parent)
                files_by_directory[directory].append(node.id)
        for file_ids in files_by_directory.values():
            for index, source in enumerate(file_ids):
                for target in file_ids[index + 1 :]:
                    graph.add_edge(source, target, "same_directory", weight=0.25)


class KnowledgeGraphStore:
    def __init__(self, path: str = DEFAULT_GRAPH_PATH) -> None:
        self.path = Path(path).expanduser().resolve()
        self.graph = self.load()

    def load(self) -> KnowledgeGraph:
        if not self.path.exists():
            return KnowledgeGraph()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return KnowledgeGraph.from_dict(payload)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.graph.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def ingest_paths(
        self,
        paths: Sequence[str | Path],
        recursive: bool = True,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> Dict[str, Any]:
        builder = KnowledgeGraphBuilder()
        incoming, files = builder.build_from_paths(paths, recursive=recursive, max_file_bytes=max_file_bytes)
        before = self.graph.summary()
        self.graph.merge(incoming)
        self.save()
        after = self.graph.summary()
        return {
            "files_processed": len(files),
            "files": [str(path) for path in files],
            "before": before,
            "after": after,
            "graph_path": str(self.path),
        }

    def search(self, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        return [
            {
                "node": asdict(result.node),
                "score": result.score,
                "edges": [asdict(edge) for edge in result.edges],
            }
            for result in self.graph.search(query, top_k=top_k)
        ]

    def rerank_chunks(
        self,
        query: str,
        chunks: Sequence[Dict[str, Any]],
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        graph_results = self.graph.search(query, top_k=max(10, top_k * 3))
        related_nodes = {result.node.id: result for result in graph_results}
        query_tokens = tokenize(query)
        centrality = self.graph.centrality_scores()
        reranked: List[Dict[str, Any]] = []

        for rank, chunk in enumerate(chunks, start=1):
            text = str(chunk.get("text", ""))
            metadata = chunk.get("metadata") or {}
            source = str(metadata.get("source") or metadata.get("path") or "")
            symbol_name = str(metadata.get("symbol_name") or "")
            distance = chunk.get("distance")
            vector_score = 0.0 if distance is None else 1.0 / (1.0 + max(float(distance), 0.0))
            lexical_score = len(query_tokens & tokenize(" ".join([text, source, symbol_name]))) / max(len(query_tokens), 1)

            graph_score = 0.0
            evidence: List[Dict[str, Any]] = []
            for result in related_nodes.values():
                node = result.node
                node_path = str(node.metadata.get("path", ""))
                path_match = source and node_path and Path(source).resolve() == Path(node_path).resolve()
                symbol_match = symbol_name and symbol_name.lower() == node.name.lower()
                text_match = node.name.lower() in text.lower() if len(node.name) > 3 else False
                if not (path_match or symbol_match or text_match):
                    continue
                boost = result.score + centrality.get(node.id, 0.0)
                if path_match:
                    boost += 2.0
                if symbol_match:
                    boost += 3.0
                if text_match:
                    boost += 0.75
                graph_score += boost
                evidence.append(
                    {
                        "node_id": node.id,
                        "kind": node.kind,
                        "name": node.name,
                        "reason": {
                            "path_match": bool(path_match),
                            "symbol_match": bool(symbol_match),
                            "text_match": bool(text_match),
                        },
                        "score": boost,
                    }
                )

            final_score = (0.55 * vector_score) + (0.25 * min(graph_score / 8.0, 1.0)) + (0.15 * lexical_score)
            final_score += 0.05 * (1.0 / rank)
            enriched = dict(chunk)
            enriched["rerank_score"] = final_score
            enriched["score_breakdown"] = {
                "vector_score": vector_score,
                "graph_score": graph_score,
                "lexical_score": lexical_score,
                "original_rank_bonus": 1.0 / rank,
            }
            enriched["graph_evidence"] = sorted(evidence, key=lambda item: item["score"], reverse=True)[:6]
            reranked.append(enriched)

        reranked.sort(key=lambda item: item["rerank_score"], reverse=True)
        return reranked[: max(1, top_k)]

    def hybrid_context(
        self,
        query: str,
        chunks: Sequence[Dict[str, Any]],
        top_k: int = 8,
        graph_depth: int = 2,
    ) -> Dict[str, Any]:
        reranked = self.rerank_chunks(query, chunks, top_k=top_k)
        subgraph = self.related_subgraph(query, depth=graph_depth, limit=50)
        return {
            "query": query,
            "reranked_chunks": reranked,
            "subgraph": subgraph,
            "graph_summary": self.summary(),
        }

    def related_subgraph(self, query: str, depth: int = 2, limit: int = 40) -> Dict[str, Any]:
        return self.graph.related_subgraph(query, depth=depth, limit=limit)

    def summary(self) -> Dict[str, Any]:
        summary = self.graph.summary()
        summary["graph_path"] = str(self.path)
        return summary
