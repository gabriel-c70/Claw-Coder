### QClawCoder
(Q-Claw + Claw-Coder)

QClawCode is a self-contained, privacy-first AI powerhouse that bridges the gap between a terminal chatbot and a autonomous developer agent. 

Built by merging the voice-enabled terminal persona Q-Claw and the pragmatic, tool-calling RAG specialist Claw-Coder, it can parse code, ingest documents, search the web, and execute shell commands—all via local models.

[![IMG-2978.webp](https://i.postimg.cc/90CktdGg/IMG-2978.webp)](https://postimg.cc/1n7MyN7D)

## Key Features

Tool-Calling Agent: Hooked directly into the Ollama Tools API. The agent autonomously decides whether to search your local knowledge base, ingest a new file, search the web, or execute terminal commands to answer your question. (Includes a graceful fallback for models that don't support tools).

Multi-Language Code RAG: Uses Tree-sitter to parse source code across 13 languages (Python, Rust, Go, C/C++/C#, JS/TS, etc.), extracting specific functions and classes rather than just blindly chunking text.
 
Knowledge Graphing: As it ingests code, it builds a local JSON knowledge graph mapping out files and symbols. It uses this graph to "rerank" vector search results for highly accurate retrieval.

Safe Terminal Execution: If the agent needs to run a command, it will. But if the command is destructive (like rm, sudo, or git push), it pauses and asks for explicit user confirmation first.

Voice & Multimodal: Born from Q-Claw, it possesses a voice. Using Kokoro TTS and Vosk STT, it can lock into a continuous "Voice Mode" where you speak to it, and it speaks back.
 
Offline Fast-Paths: Knows when not to bother the AI. If you say "hi", it responds instantly. If you ask for a quick web search, it bypasses the LLM entirely and fetches summaries from Wikipedia and DuckDuckGo.

## Installation & Setup

'''bash

1. Prerequisites
Ensure you have Ollama installed and running on your machine.

ollama pull llama3.2:3bollama pull qwen3-embedding:4b  # Used for vector embeddings

2. Install QClawCode
Clone the repository and run the built-in setup command to install Python dependencies:
￼
git clone https://github.com/gabriel-c70/Claw-Coder.git
cd Claw-Coder
python Qclawcode.py setup

3. Verify Setup
Run the doctor command to ensure your Node, Python, and Ollama environments are configured correctly:

python Qclawcode.py doctor

Usage

Start the interactive chat REPL (default):

python Qclawcode.py

'''

[![IMG-3066.webp](https://i.postimg.cc/FHG1py8k/IMG-3066.webp)](https://postimg.cc/rD08Fr8q)
