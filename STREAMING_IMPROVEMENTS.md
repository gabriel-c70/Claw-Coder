# Claw-Coder Streaming, Ollama Stability, and Multi-line Input Improvements

## Summary of Changes

This document describes the improvements made to address three key issues:
1. **Ollama termination issues on GitHub Codespaces**
2. **Lack of streaming output (responses came as one big final answer)**
3. **Better multi-line input editing experience**

## 1. Ollama Termination Issues

### Root Causes Identified
- GitHub Codespaces has resource constraints that can cause Ollama processes to terminate
- Insufficient retry logic when Ollama connections fail
- Lack of proper process management and monitoring
- Missing environment variables for Ollama stability in constrained environments

### Solutions Implemented

#### a) Enhanced `ensure_ollama_running()` function in `agent_rag.py`
- Added environment variables for better stability:
  - `OLLAMA_KEEP_ALIVE=-1` - Keep models loaded indefinitely
  - `OLLAMA_NUM_LOAD_RETRY=10` - Retry loading models more times
  - `OLLAMA_LOAD_TIMEOUT=10m` - Longer timeout for loading models
  - `OLLAMA_MAX_QUEUE=512` - Allow more queued requests
- Increased startup wait time from 15 to 30 seconds
- Better error handling and logging

#### b) Improved retry logic in `_ollama_chat_with_retry()`
- Increased retry attempts from 5 to 7
- Enhanced exponential backoff: 3, 6, 9, 12, 15, 18 seconds
- Automatic Ollama restart detection and recovery
- Better error detection for "terminated", "connection", "refused", "500", "502", "503", "timeout", "signal" errors

#### c) Enhanced remote workspace Ollama management in `workspace.py`
- Added robust process management for remote Ollama instances
- Implemented process cleanup before starting new instances
- Added verification after startup to ensure Ollama is actually running
- Better error logging with log file inspection

## 2. Streaming Output Implementation

### Architecture Changes

#### a) Modified `agent_rag.py`
- Updated `_ollama_chat_with_retry()` to accept optional `stream_callback` parameter
- When callback is provided, uses `stream=True` in ollama.chat()
- Streams content chunks to callback as they arrive
- Maintains backward compatibility (non-streaming when no callback)

#### b) Updated `workspace.py`
- Modified `REMOTE_CHAT_SCRIPT` to support streaming mode
- Added `stream` parameter to payload
- When streaming, outputs JSON chunks with `{"chunk": content}` format
- Sends final response with `{"final": response}` format
- Added `_remote_python_stream()` method for handling streaming over SSH
- Updated `_chat_http()` to support streaming over HTTP API (RunPod)

#### c) Enhanced chat loop in `agent_rag.py`
- Added streaming callback in the main chat loop
- Callback uses Rich library for real-time display when available
- Falls back to basic print when Rich is not available
- Maintains full response for tool calls and memory

### Key Features

1. **Real-time Output**: Users see responses as they're generated, not waiting for completion
2. **Backward Compatible**: Non-streaming mode still works when needed
3. **Multi-mode Support**: Works with local Ollama, SSH workspace, and HTTP API
4. **Rich Integration**: Leverages Rich library for enhanced display when available
5. **Error Resilient**: Streaming failures fall back to non-streaming mode

## Testing

All changes have been validated with:
- Python syntax compilation checks
- Streaming callback mechanism verification
- Rich library availability check
- Ollama connectivity verification

## Usage

The streaming is now automatic for all chat interactions. Users don't need to enable anything - the system will:
1. Stream responses in real-time when possible
2. Fall back to non-streaming if streaming fails
3. Provide better error recovery for Ollama termination issues
4. Work seamlessly across local, SSH, and HTTP workspace modes

## Benefits

1. **Better User Experience**: Real-time feedback instead of waiting for complete responses
2. **Improved Reliability**: Enhanced retry logic and error handling
3. **Resource Efficiency**: Better process management in constrained environments
4. **Future-Proof**: Architecture supports additional streaming enhancements

## Files Modified

1. `agent_rag.py` - Core streaming implementation, retry logic, and multi-line input integration
2. `workspace.py` - Remote workspace streaming support
3. `claw_ui.py` - UI improvements and multi-line input functionality
4. `requirements.txt` - Added prompt_toolkit dependency

## Technical Details

### Streaming Flow

```
User Input → Agent.chat() → stream_callback → Real-time Display
                                    ↓
                             ollama.chat(stream=True)
                                    ↓
                             Process chunks as they arrive
```

### Error Recovery Flow

```
Connection Error → Retry with Backoff → Attempt Ollama Restart → Retry Again
                                   ↓
                           Max Retries Reached → Graceful Error Message
```

### Remote Workspace Streaming

```
Local Agent → SSH/HTTP → Remote Script → ollama.chat(stream=True) → JSON Chunks → Local Callback → Display
```

## 3. Enhanced Multi-line Input Experience

### Problem Addressed
Users wanted a better way to edit multi-line input directly in the terminal, similar to modern AI interfaces like Claude, rather than using external editors.

### Solution Implemented

#### a) New `read_multiline_input()` function in `claw_ui.py`
- Uses `prompt_toolkit` library for rich multi-line editing
- Shows placeholder "work with claw-coder on a complex project" when input is empty
- Changes to simple `❭ ` prompt when user starts typing
- Supports keyboard shortcuts:
  - `Ctrl+D` - Accept and send the input
  - `Ctrl+C` - Cancel input
- Multi-line support with proper text editing capabilities
- Falls back to simple multi-line input if `prompt_toolkit` is not available

#### b) Updated chat interface
- Replaced single-line input with multi-line input by default
- All commands now work within the multi-line context
- Better visual feedback with the prompt indicator
- Updated help text to reflect new input method

### Key Features

1. **Inline Editing**: Edit text directly in the terminal without external editors
2. **Multi-line Support**: Write complex prompts with proper formatting
3. **Keyboard Shortcuts**: Intuitive shortcuts for accepting/canceling input
4. **Dynamic Placeholder**: Shows "work with claw-coder on a complex project" when empty, changes to `❭ ` when typing
5. **Fallback Support**: Works even if `prompt_toolkit` is not installed

### Usage

```bash
# During a chat session:
# Shows placeholder when empty:
work with claw-coder on a complex project ❭ 

# When you start typing, placeholder disappears:
❭ I want to write a complex prompt
that spans multiple lines and has proper formatting.

# Press Ctrl+D to accept and send
# Press Ctrl+C to cancel

# Commands work the same way:
❭ /help
❭ /models
❭ exit
```

### Dependencies

Added `prompt_toolkit>=3.0.0` to requirements.txt for enhanced multi-line editing.

### Technical Details

#### Multi-line Input Flow

```
Display prompt → User types multi-line text → Edit using arrow keys → Ctrl+D to accept → Send to Claw-Coder
```

#### Keyboard Shortcuts

- `Ctrl+D` - Accept input and send to Claw-Coder
- `Ctrl+C` - Cancel current input
- Arrow keys - Navigate within the text
- Standard terminal editing - Delete, Home, End, etc.

#### Error Handling

```
prompt_toolkit not available → Fall back to simple multi-line input → Empty line to finish
```

## Future Enhancements

Potential improvements for future versions:
- Progress indicators for long-running operations
- Configurable streaming vs non-streaming modes
- Enhanced chunk size optimization
- Better visual feedback during tool execution
- Syntax highlighting in multi-line input mode
- Auto-completion for commands and code snippets