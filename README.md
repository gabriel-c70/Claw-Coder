# Claw Coder

Claw Coder is a local CLI agent with Tree-sitter code understanding, a persistent
knowledge graph, ChromaDB vector RAG, graph-aware reranking, Ollama chat, and
multi-file ingestion.

## Install

From this directory:

```bash
npm install -g .
claw setup
```

For development, use a symlink instead:

```bash
npm link
claw setup
```

`claw setup` installs the Python dependencies from `requirements.txt`. You also
need Ollama running for chat, embeddings, and vector RAG:

```bash
ollama serve
claw <model>
claw <chat model> <embedding modal>
```

## Use

```bash
claw doctor
claw languages
claw ingest .
claw graph "tree_sitter imports" --depth 2
claw search "where is graph reranking implemented?" --top-k 5
claw chat
```

Useful options:

```bash
claw ingest ./src --no-vector-rag
claw search "authentication flow" --graph ./my_graph.json --db ./rag_db
claw graph "calls run_terminal" --top-k 10 --depth 3
```

You can also use the longer binary name:

```bash
claw-coder doctor
```
