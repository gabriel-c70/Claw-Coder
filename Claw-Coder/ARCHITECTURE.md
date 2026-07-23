# Claw-Coder Architecture Documentation

## Overview

Claw-Coder is a local-first AI agent that transforms small local LLMs into powerful coding assistants. It combines knowledge graphs, vector RAG, tree-sitter parsing, and various tools to provide intelligent code understanding and generation capabilities.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Interface Layer                      │
├─────────────────────────────────────────────────────────────────┤
│  Node.js CLI (bin/claw-coder.js)  →  Python Agent (agent_rag.py) │
│  Rich UI (claw_ui.py)              →  Textual UI (claw_textual_ui.py)│
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                          Core Agent Layer                        │
├─────────────────────────────────────────────────────────────────┤
│  Agent Class (agent_rag.py) - Main orchestration logic          │
│  - Tool execution & management                                   │
│  - Chat loop & conversation handling                             │
│  - Model integration (Ollama)                                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      Knowledge & RAG Layer                      │
├─────────────────────────────────────────────────────────────────┤
│  Knowledge Graph (agent_knowledge.py)  ←  Vector RAG (ChromaDB) │
│  - Tree-sitter parsing                  - Embeddings (Ollama)     │
│  - Code relationship mapping            - Semantic search        │
│  - File structure understanding         - Hybrid reranking       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                         Tools Layer                              │
├─────────────────────────────────────────────────────────────────┤
│  File Operations | Docker | Search | Git | Terminal | Workspace  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Infrastructure Layer                          │
├─────────────────────────────────────────────────────────────────┤
│  API Server (agent_sever.py)  ←  Auth (bin/auth.js)             │
│  - Rate limiting                 - GitHub OAuth                  │
│  - Credit billing                - Session management            │
│  - Tool usage tracking           - Supabase integration         │
└─────────────────────────────────────────────────────────────────┘
```

## Component Deep Dive

### 1. User Interface Layer

#### **bin/claw-coder.js** (Node.js CLI Entry Point)
- **Purpose**: Main command-line interface and entry point
- **Key Functions**:
  - Command parsing and routing
  - Environment setup (Python detection, virtual environment management)
  - Ollama installation and management
  - Authentication integration
  - Session management
- **Data Flow**: 
  - Receives user commands → Parses arguments → Calls Python agent
  - Handles setup/doctor commands directly
  - Routes chat/ingest/search commands to Python backend

#### **claw_ui.py** (Rich-based Terminal UI)
- **Purpose**: Beautiful terminal interface using Rich library
- **Key Components**:
  - Model selection interface
  - Chat formatting and display
  - Progress indicators and spinners
  - Terminal title management
  - Workspace prompts
- **Styling**: Uses Rich for colors, panels, tables, and markdown rendering

#### **claw_textual_ui.py** (Modern Textual UI)
- **Purpose**: Advanced TUI with scrolling and selection
- **Key Features**:
  - Interactive chat interface with scrollable history
  - Command palette (Ctrl+P) for quick command access
  - Model selector (Ctrl+M) with table-based selection
  - Keyboard navigation (↑/↓ scrolling, Ctrl+R clear)
  - Sidebar with status and help information
- **Architecture**: Built on Textual framework with modal screens

### 2. Core Agent Layer

#### **agent_rag.py** (Main Agent Implementation)
- **Purpose**: Central orchestration of all AI capabilities
- **Key Classes**:
  - `Agent`: Main agent class with tool execution and chat logic
  - `Tool`: Individual tool implementations
- **Core Functions**:
  - **Chat Loop**: Handles conversation flow and context management
  - **Tool Execution**: Routes requests to appropriate tools
  - **Model Integration**: Interfaces with Ollama for chat and embeddings
  - **RAG Integration**: Combines vector search and knowledge graph queries
- **Data Flow**:
  ```
  User Input → Agent.chat() → Tool Selection → Tool Execution → 
  Response Generation → Knowledge Retrieval → Final Response
  ```

### 3. Knowledge & RAG Layer

#### **agent_knowledge.py** (Knowledge Graph)
- **Purpose**: Explicit code relationship mapping and structure understanding
- **Key Components**:
  - `KnowledgeGraphStore`: Main graph storage and query engine
  - Tree-sitter integration for code parsing
  - Relationship tracking (defines, calls, imports, contains)
  - Entity extraction and symbol resolution
- **Data Structures**:
  - Nodes: Files, symbols, functions, classes, chunks
  - Edges: Typed relationships with weights
  - Metadata: File paths, line numbers, symbol types
- **Query Types**:
  - Symbol search: Find definitions and references
  - Relationship traversal: Follow call chains and import dependencies
  - Graph ranking: Weighted path analysis for relevance scoring

#### **Vector RAG (ChromaDB + Ollama Embeddings)**
- **Purpose**: Semantic search and content retrieval
- **Integration**:
  - ChromaDB for vector storage and similarity search
  - Ollama for text embeddings (qwen3-embedding:4b)
  - Hybrid reranking combining vector scores with graph scores
- **Workflow**:
  ```
  Document Ingestion → Chunking → Embedding Generation → 
  Vector Storage → Query Embedding → Similarity Search → 
  Graph Reranking → Final Results
  ```

### 4. Tools Layer

#### **File Operations Tools**
- `read_files`: Read file contents with metadata
- `list_files`: Directory traversal and file listing
- `edit_file`: Modify file contents with validation
- `create_file`: Create new files with templates
- `delete_file`: Safe file deletion with confirmation
- `search_code`: Search across codebase with patterns

#### **Docker Tools**
- `execute_code_in_docker`: Run code in isolated containers
- Container management for safe code execution
- Volume mounting for file access
- Resource limiting and timeout handling

#### **Search Tools**
- `search_stuff`: Web search via Tavily API
- Real-time information retrieval
- Hallucination reduction through current data
- Rate limiting and credit billing

#### **Git Tools**
- `git_diff`: Show changes between commits/branches
- `git_status`: Show working directory status
- `git_apply_patch`: Apply patches with conflict resolution
- Version control integration for code changes

#### **Terminal Tools**
- `run_terminal`: Execute shell commands
- Output capture and error handling
- Working directory management
- Interactive command support

#### **Workspace Tools**
- Remote workspace support via SSH
- GitHub Codespace integration
- Remote agent execution
- File synchronization

### 5. Infrastructure Layer

#### **agent_sever.py** (FastAPI Server)
- **Purpose**: Rate limiting, billing, and usage tracking
- **Key Endpoints**:
  - `/check`: Tool usage rate limiting
  - `/search`: Proxied web search with rate limiting
  - `/workspace/connect`: Workspace connection management
  - `/usage`: Usage statistics and credit information
  - `/plan`: User plan and credit balance
  - `/checkout`: Payment processing via Dodo Payments
  - `/webhooks/dodo`: Payment webhook handling
- **Credit System**:
  - Bucket-based credit management (tools vs workspace)
  - Tool-specific credit costs
  - Monthly subscriptions and top-ups
  - Usage tracking and reporting

#### **bin/auth.js** (Authentication System)
- **Purpose**: User authentication and session management
- **Key Functions**:
  - GitHub OAuth device flow
  - Supabase user management
  - Session token storage and validation
  - Automatic session refresh
- **Security**:
  - Secure session file storage (0o600 permissions)
  - Token expiration handling
  - Environment-based configuration

#### **workspace.py** (Remote Workspace)
- **Purpose**: Remote execution support for cloud workspaces
- **Key Components**:
  - `WorkspaceConfig`: Configuration for remote connections
  - `WorkspaceRemoteClient`: SSH-based remote execution
- **Workflow**:
  ```
  Local UI → SSH Connection → Remote Agent Execution → 
  Result Return → Local Display
  ```

## Data Flow Architecture

### **Chat Flow**
```
User Input (CLI) 
    ↓
Command Parser (claw-coder.js)
    ↓
Python Agent (agent_rag.py)
    ↓
Tool Selection & Execution
    ↓
Knowledge Retrieval (Knowledge Graph + Vector RAG)
    ↓
Model Inference (Ollama)
    ↓
Response Generation
    ↓
UI Display (Rich/Textual)
```

### **Ingestion Flow**
```
File/Directory Input
    ↓
Tree-sitter Parsing (agent_knowledge.py)
    ↓
Code Structure Analysis
    ↓
Knowledge Graph Construction
    ↓
Chunking & Embedding (Ollama)
    ↓
Vector Storage (ChromaDB)
    ↓
Indexing & Metadata Update
```

### **Tool Execution Flow**
```
Tool Request
    ↓
Rate Limit Check (agent_sever.py)
    ↓
Credit Verification
    ↓
Tool Execution
    ↓
Result Processing
    ↓
Credit Deduction
    ↓
Result Return
```

## Configuration & Environment

### **Environment Variables**
- `CLAW_MODEL`: Default Ollama model for chat
- `CLAW_EMBEDDING_MODEL`: Default embedding model
- `RATE_LIMIT_API_URL`: API server URL for rate limiting
- `SUPABASE_URL`: Supabase backend URL
- `SUPABASE_SERVICE_KEY`: Supabase service role key
- `DODO_PAYMENTS_API_KEY`: Payment processing API key

### **Data Files**
- `agent_knowledge_graph.json`: Knowledge graph storage
- `agent_memory.json`: Conversation memory
- `agent_rag_chroma_db/`: Vector database storage
- `.claw-coder/session.json`: User authentication session

### **Dependencies**
- **Python**: ollama, chromadb, tree-sitter, rich, textual, fastapi, supabase
- **Node.js**: Used for CLI wrapper and authentication
- **Ollama**: Local LLM runtime for models and embeddings

## Security & Privacy

### **Local-First Architecture**
- All AI processing happens locally via Ollama
- Knowledge graphs and vector stores stored locally
- No code sent to external AI services (except search API)

### **Authentication**
- GitHub OAuth for user identification
- Supabase for user management and billing
- Secure session storage with file permissions

### **Rate Limiting & Billing**
- Server-side rate limiting for expensive tools
- Credit-based billing for pro features
- Separate buckets for tools and workspace credits
- Usage tracking and reporting

## Extension Points

### **Adding New Tools**
1. Implement tool function in `agent_rag.py`
2. Add to tool registry with metadata
3. Configure credit cost in `agent_sever.py`
4. Update rate limits if needed

### **Adding New Languages**
1. Add tree-sitter language binding
2. Update `LANGUAGE_SPECS` in `agent_knowledge.py`
3. Add to `SUPPORTED_TEXT_EXTENSIONS`
4. Install language parser

### **Custom UI Components**
1. Extend `claw_ui.py` for Rich components
2. Extend `claw_textual_ui.py` for Textual components
3. Add command-line arguments for UI selection
4. Integrate with main chat loop

## Performance Optimizations

### **Knowledge Graph**
- Lazy loading of file contents
- Incremental graph updates
- Relationship weight optimization
- Symbol caching

### **Vector RAG**
- Chunk overlap for context preservation
- Hybrid reranking for accuracy
- Efficient similarity search
- Batch embedding generation

### **Tool Execution**
- Async operations where possible
- Resource limiting and timeouts
- Result caching for repeated operations
- Efficient file I/O

## Error Handling & Resilience

### **Graceful Degradation**
- Fallback from Textual to Rich UI if not available
- Fallback from knowledge graph to pure vector search
- Offline mode for local operations
- Partial failure handling in tool execution

### **Logging & Debugging**
- Comprehensive logging to file and console
- Error context preservation
- Stack trace capture for debugging
- User-friendly error messages

## Conclusion

Claw-Coder represents a sophisticated integration of multiple AI and software engineering technologies:

1. **Local AI Processing**: Leverages Ollama for privacy-focused local inference
2. **Advanced Code Understanding**: Combines knowledge graphs with vector RAG
3. **Tool Integration**: Provides comprehensive development tools
4. **Modern UI**: Offers both traditional and advanced terminal interfaces
5. **Commercial Viability**: Includes billing, rate limiting, and user management
6. **Extensibility**: Designed for easy addition of languages, tools, and features

The architecture balances performance, privacy, usability, and commercial viability while maintaining a local-first approach to AI-assisted development.