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
from typing import Any, Dict, List, Optional, Sequence

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.spinner import Spinner
    from rich.status import Status
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

DEFAULT_TAB_PREFIX = "Claw-Coder"


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


def conversation_title_from_message(message: str, max_len: int = 40) -> str:

    """Derive a short tab title from the user's first message using cloud API or local Ollama."""
    import json
    import urllib.request
    import urllib.error
    import ssl
    from pathlib import Path
    
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
                            return response.get("title", f"{DEFAULT_TAB_PREFIX} · {text[:max_len]}")
                except urllib.error.HTTPError as exc:
                    # Fall back to local generation if cloud fails
                    pass
                except Exception:
                    # Fall back to local generation if cloud fails
                    pass
    except Exception:
        # Fall back to local generation if session or cloud fails
        pass
    
    # Fall back to local Ollama for AI generation
    try:
        import ollama
        
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
            model="llama3.2:1b",
            messages=[{"role": "user", "content": prefix_prompt}]
        )
        generate_title = response["message"]["content"].strip()
        
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
        
        return f"{DEFAULT_TAB_PREFIX} · {generate_title}"
        
    except Exception:
        # Final fallback - use truncated text
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
            task = progress.add_task(f"Pulling {model_name}", total=None)
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

    _console().print(Panel(table, title="[bold]Local Ollama models[/bold]", border_style="cyan"))
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
        print(f"Claw Coder — model: {model} | embeddings: {embedding_model}")
        return

    # Show simple welcome box
    show_simple_welcome_box()
    
    body = Text()
    body.append("Claw Coder\n", style="bold cyan")
    body.append(f"chat  {model}\n", style="white")
    body.append(f"embed {embedding_model}\n", style="dim")
    body.append("\nCommands: /models /pdf <file> /help /title  exit\n", style="dim italic")
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


def print_assistant_start() -> None:
    if RICH_AVAILABLE:
        _console().print("[bold cyan]Claw[/bold cyan]")
    else:
        print("Claw> ", end="", flush=True)


def print_assistant_response(text: str) -> None:
    if not text:
        return
    if RICH_AVAILABLE:
        _console().print(Markdown(text))
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
