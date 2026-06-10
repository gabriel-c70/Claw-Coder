# Claw-Coder
This is a RAG AI agent with sorts of capabilities.

## Installation

Claw Coder installs the `claw` command. After installing, run `claw setup` once
to install the Python dependencies, then `claw doctor` to verify your setup.

### macOS / Linux (Homebrew)

```sh
brew tap gabriel-c70/claw-coder
brew install claw-coder
claw setup
```

### Windows (Scoop)

```powershell
scoop bucket add claw https://github.com/gabriel-c70/homebrew-claw-coder
scoop install claw-coder
claw setup
```

Both methods are defined in the
[gabriel-c70/homebrew-claw-coder](https://github.com/gabriel-c70/homebrew-claw-coder)
repo (Homebrew formula + Scoop bucket).

## Usage

```sh
claw chat            # start the interactive agent
claw ingest .        # ingest files into the knowledge graph + vector RAG
claw search "query"  # search with graph reranking
claw doctor          # check your Node/Python/Ollama setup
```

Run `claw --help` for the full list of commands.
