"""
Modern Textual-based UI for Claw Coder with scrolling, selection, and keyboard navigation.
"""

from __future__ import annotations

import asyncio
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
    from textual.widgets import (
        Header, Footer, Input, Button, Static, Markdown, 
        ListView, ListItem, Label, ProgressBar, Select, DataTable
    )
    from textual.reactive import reactive
    from textual import events
    from textual.binding import Binding
    from textual.message import Message
    from textual.screen import ModalScreen
    
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False


class ChatMessage(Static):
    """A widget to display a chat message."""
    
    def __init__(self, role: str, content: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.role = role
        self.content = content
        
    def render(self) -> str:
        if self.role == "user":
            return f"[bold cyan]You:[/bold cyan] {self.content}"
        elif self.role == "assistant":
            return f"[bold green]Claw:[/bold green] {self.content}"
        elif self.role == "system":
            return f"[dim]{self.content}[/dim]"
        return self.content


class CommandPalette(ModalScreen):
    """A modal screen for command selection."""
    
    def __init__(self, commands: List[Dict[str, Any]], **kwargs) -> None:
        super().__init__(**kwargs)
        self.commands = commands
        self.selected_command = None
        
    def compose(self) -> ComposeResult:
        with Container(id="command-container"):
            yield Label("Select a command:", id="command-label")
            yield ListView(
                *[ListItem(Label(cmd["name"])) for cmd in self.commands],
                id="command-list"
            )
            with Horizontal(id="command-buttons"):
                yield Button("Select", id="select-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "select-btn":
            list_view = self.query_one(ListView)
            if list_view.index is not None:
                self.selected_command = self.commands[list_view.index]
                self.dismiss(self.selected_command)
        else:
            self.dismiss(None)
    
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.selected_command = self.commands[event.list_view.index]
        self.dismiss(self.selected_command)


class ModelSelector(ModalScreen):
    """A modal screen for model selection."""
    
    def __init__(self, models: List[Dict[str, Any]], **kwargs) -> None:
        super().__init__(**kwargs)
        self.models = models
        self.selected_model = None
        
    def compose(self) -> ComposeResult:
        with Container(id="model-container"):
            yield Label("Select a model:", id="model-label")
            
            # Create a data table for models
            table = DataTable()
            table.add_column("Model", key="name")
            table.add_column("Size", key="size")
            
            for model in self.models:
                size = self._format_size(model.get("size"))
                table.add_row(model["name"], size)
            
            table.id = "model-table"
            yield table
            
            with Horizontal(id="model-buttons"):
                yield Button("Select", id="select-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn")
    
    def _format_size(self, size: Any) -> str:
        try:
            size = float(size)
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if size < 1024:
                    return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
                size /= 1024
            return f"{size:.1f} PB"
        except (TypeError, ValueError):
            return "—"
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "select-btn":
            table = self.query_one(DataTable)
            if table.cursor_row is not None:
                self.selected_model = self.models[table.cursor_row]
                self.dismiss(self.selected_model)
        else:
            self.dismiss(None)
    
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_model = self.models[event.row_key]
        self.dismiss(self.selected_model)


class ClawChatApp(App):
    """Main Textual application for Claw Coder chat interface."""
    
    CSS = """
    Screen {
        background: #0f172a;
    }
    
    #header {
        background: #1e293b;
        text-align: center;
        padding: 1;
    }
    
    #chat-container {
        height: 1fr;
        border: solid #334155;
        padding: 1;
    }
    
    #input-container {
        height: 5;
        border: solid #334155;
        padding: 1;
        dock: bottom;
    }
    
    #input-area {
        height: 3;
    }
    
    #sidebar {
        width: 25;
        border: solid #334155;
        padding: 1;
        dock: left;
    }
    
    #status-bar {
        height: 3;
        border: solid #334155;
        padding: 1;
        dock: bottom;
    }
    
    ChatMessage {
        padding: 1;
        margin: 1;
        background: #1e293b;
        border: solid #334155;
    }
    
    Button {
        margin: 1;
    }
    
    #command-container, #model-container {
        padding: 2;
        background: #1e293b;
        border: solid #334155;
    }
    
    #command-list, #model-table {
        height: 20;
    }
    
    #command-buttons, #model-buttons {
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+p", "show_commands", "Commands", show=True),
        Binding("ctrl+m", "show_models", "Models", show=True),
        Binding("ctrl+r", "clear_chat", "Clear", show=True),
        Binding("up", "scroll_up", "Scroll Up", show=False),
        Binding("down", "scroll_down", "Scroll Down", show=False),
    ]
    
    def __init__(self, agent, **kwargs) -> None:
        super().__init__(**kwargs)
        self.agent = agent
        self.messages: List[Dict[str, str]] = []
        self.on_message_callback: Optional[Callable] = None
        self.commands = [
            {"name": "/models", "description": "List available models"},
            {"name": "/model <name>", "description": "Switch to specific model"},
            {"name": "/workspace", "description": "Connect to remote workspace"},
            {"name": "/workspace status", "description": "Show workspace status"},
            {"name": "/workspace local", "description": "Switch to local mode"},
            {"name": "/workspace pull <model>", "description": "Pull model on remote"},
            {"name": "/pdf <file>", "description": "Load PDF document"},
            {"name": "/title", "description": "Set conversation title"},
            {"name": "/help", "description": "Show help"},
            {"name": "/clear", "description": "Clear chat history"},
            {"name": "exit", "description": "Exit the application"},
        ]
    
    def compose(self) -> ComposeResult:
        yield Header()
        
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("🦙 Claw Coder", id="app-title")
                yield Label(f"Model: {self.agent.model}", id="model-label")
                yield Label(f"Embed: {self.agent.embedding_model}", id="embed-label")
                yield Label("", id="spacer")
                yield Label("Commands:", id="commands-title")
                yield Label("Ctrl+P: Commands", id="cmd-help-1")
                yield Label("Ctrl+M: Models", id="cmd-help-2")
                yield Label("Ctrl+R: Clear", id="cmd-help-3")
                yield Label("↑/↓: Scroll", id="cmd-help-4")
            
            with Vertical(id="main-area"):
                with ScrollableContainer(id="chat-container"):
                    yield Label("Chat will appear here...", id="welcome-message")
                
                with Container(id="input-container"):
                    yield Input(placeholder="Type your message here...", id="input-area")
                    yield Button("Send", id="send-btn", variant="primary")
        
        yield Label("Ready - Type a message to start chatting", id="status-bar")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self.send_message()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.send_message()
    
    def send_message(self) -> None:
        input_widget = self.query_one("#input-area", Input)
        message = input_widget.value.strip()
        
        if not message:
            return
        
        if message.lower() in {"exit", "quit", "/exit", "/quit"}:
            self.exit()
            return
        
        if message.lower() in {"/help", "help"}:
            self.show_help()
            input_widget.value = ""
            return
        
        if message.lower() == "/clear":
            self.clear_chat()
            input_widget.value = ""
            return
        
        if message.lower() == "/models":
            self.show_models()
            input_widget.value = ""
            return
        
        if message.lower().startswith("/model "):
            model_name = message.split(" ", 1)[1].strip()
            self.switch_model(model_name)
            input_widget.value = ""
            return
        
        # Handle workspace commands
        if message.lower().startswith("/workspace"):
            self.handle_workspace_command(message)
            input_widget.value = ""
            return
        
        # Regular chat message
        self.add_message("user", message)
        input_widget.value = ""
        
        # Process the message through the agent
        self.process_message(message)
    
    def handle_workspace_command(self, message: str) -> None:
        """Handle workspace-related commands."""
        parts = message.split()
        
        if len(parts) == 1:
            # Just /workspace - prompt for connection
            self.add_message("system", "Please provide your SSH connection details:")
            self.add_message("system", "• GitHub Codespaces: ssh cs.your-codespace-name")
            self.add_message("system", "• Regular SSH: ssh user@hostname")
            self.add_message("system", "• With port: ssh user@hostname:port")
            self.add_message("system", "• IP address: ssh user@192.168.1.1")
            self.add_message("system", "• SSH alias: your-ssh-config-alias")
            self.add_message("system", "Use: /workspace <your-ssh-target>")
            return
        
        if parts[1] in {"status", "show"}:
            if self.agent.remote_workspace:
                status = self.agent.remote_workspace.status()
                self.add_message("system", status)
            else:
                self.add_message("system", "Workspace mode is not available.")
            return
        
        if parts[1] == "local":
            self.agent.workspace_mode = "local"
            if self.agent.remote_workspace:
                self.agent.remote_workspace.config.mode = "local"
            self.add_message("system", "Workspace mode set to local for this session.")
            return
        
        if parts[1] == "pull" and len(parts) >= 3:
            if not self.agent.remote_workspace:
                self.add_message("system", "Workspace mode is unavailable.")
                return
            model_name = " ".join(parts[2:])
            result = self.agent.remote_workspace.pull_model(model_name)
            self.add_message("system", result)
            return
        
        # Treat as connection target
        target = " ".join(parts[1:])
        try:
            result = self.agent.setup_workspace_from_paste(target)
            self.add_message("system", result)
        except Exception as e:
            self.add_message("system", f"Error connecting to workspace: {str(e)}")
    
    def add_message(self, role: str, content: str) -> None:
        # Remove welcome message if it exists
        welcome = self.query_one("#welcome-message", Label, expect_none=True)
        if welcome:
            welcome.remove()
        
        # Add message to chat
        chat_container = self.query_one("#chat-container", ScrollableContainer)
        message_widget = ChatMessage(role, content)
        chat_container.mount(message_widget)
        chat_container.scroll_end()
        
        self.messages.append({"role": role, "content": content})
    
    def process_message(self, message: str) -> None:
        """Process a message through the agent (async)."""
        status_bar = self.query_one("#status-bar", Label)
        status_bar.update("🤔 Being Creative...")
        
        async def process():
            try:
                response = await asyncio.to_thread(self.agent.chat, message)
                self.add_message("assistant", response)
                status_bar.update("✓ Ready")
            except Exception as e:
                self.add_message("system", f"Error: {str(e)}")
                status_bar.update(f"✗ Error: {str(e)}")
        
        asyncio.create_task(process())
    
    def action_show_commands(self) -> None:
        """Show the command palette."""
        def handle_command(command):
            if command:
                input_widget = self.query_one("#input-area", Input)
                input_widget.value = command["name"]
                input_widget.focus()
        
        self.push_screen(CommandPalette(self.commands), handle_command)
    
    def action_show_models(self) -> None:
        """Show the model selector."""
        try:
            from claw_ui import list_ollama_models
            models = list_ollama_models()
            
            def handle_model(model):
                if model:
                    self.switch_model(model["name"])
            
            self.push_screen(ModelSelector(models), handle_model)
        except Exception as e:
            self.add_message("system", f"Error loading models: {str(e)}")
    
    def action_clear_chat(self) -> None:
        """Clear the chat history."""
        self.clear_chat()
    
    def action_scroll_up(self) -> None:
        """Scroll up in the chat container."""
        chat_container = self.query_one("#chat-container", ScrollableContainer)
        chat_container.scroll_up()
    
    def action_scroll_down(self) -> None:
        """Scroll down in the chat container."""
        chat_container = self.query_one("#chat-container", ScrollableContainer)
        chat_container.scroll_down()
    
    def clear_chat(self) -> None:
        """Clear all messages from the chat."""
        chat_container = self.query_one("#chat-container", ScrollableContainer)
        chat_container.remove_children()
        chat_container.mount(Label("Chat cleared. Type a message to start...", id="welcome-message"))
        self.messages.clear()
        self.query_one("#status-bar", Label).update("Chat cleared")
    
    def show_help(self) -> None:
        """Show help information."""
        help_text = """
        Available Commands:
        • /models - List available models
        • /model <name> - Switch to specific model
        • /workspace - Connect to remote workspace
        • /workspace status - Show workspace status
        • /workspace local - Switch to local mode
        • /workspace pull <model> - Pull model on remote
        • /pdf <file> - Load PDF document
        • /title - Set conversation title
        • /clear - Clear chat history
        • exit - Exit the application
        
        Workspace Connection Formats:
        • GitHub Codespaces: ssh cs.your-codespace-name
        • Regular SSH: ssh user@hostname
        • With port: ssh user@hostname:port
        • IP address: ssh user@192.168.1.1
        • SSH alias: your-ssh-config-alias
        • Codespaces URL: https://github.com/codespaces/...
        
        Keyboard Shortcuts:
        • Ctrl+P - Show command palette
        • Ctrl+M - Show model selector
        • Ctrl+R - Clear chat
        • ↑/↓ - Scroll through chat
        • Ctrl+C - Quit
        """
        self.add_message("system", help_text.strip())
    
    def switch_model(self, model_name: str) -> None:
        """Switch to a different model."""
        try:
            from claw_ui import validate_ollama_model
            validated_model = validate_ollama_model(model_name)
            self.agent.switch_model(validated_model)
            self.query_one("#model-label", Label).update(f"Model: {validated_model}")
            self.add_message("system", f"Switched to model: {validated_model}")
            self.query_one("#status-bar", Label).update(f"✓ Switched to {validated_model}")
        except Exception as e:
            self.add_message("system", f"Error switching model: {str(e)}")
            self.query_one("#status-bar", Label).update(f"✗ Error: {str(e)}")


def run_textual_chat(agent, document_paths: Optional[List[str]] = None) -> None:
    """Run the Textual-based chat interface. This is now the only chat UI."""
    if not TEXTUAL_AVAILABLE:
        print("Textual is not installed. Run: pip install textual>=0.44.0")
        print("Falling back to Rich-based UI...")
        from agent_rag import run_interactive_chat
        run_interactive_chat(agent, document_paths=document_paths)
        return

    if document_paths:
        from agent_rag import ingest_session_documents
        ingest_session_documents(agent, document_paths)

    app = ClawChatApp(agent)
    app.run()


if __name__ == "__main__":
    print("Claw Textual UI Module")
    print("This module provides an improved terminal UI with scrolling and selection.")
    print("Import and use run_textual_chat(agent) to start the chat interface.")