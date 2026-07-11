"""
Terminal UI helpers for Claw Coder: rich formatting, model selection, tab titles.
"""

from __future__ import annotations

import os
import re
import sys
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

DEFAULT_TAB_PREFIX = "Claw"


def _console() -> "Console":
    if not RICH_AVAILABLE:
        raise RuntimeError("rich is not installed. Run: claw setup")
    return Console(highlight=False)


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


def conversation_title_from_message(message: str, max_len: int = 48) -> str:
    """Derive a short tab title from the user's first message."""
    text = " ".join(message.strip().split())
    if not text:
        return DEFAULT_TAB_PREFIX

    text = re.sub(r"^[/!@#]+\s*", "", text)
    for prefix in ("please ", "can you ", "could you ", "help me ", "i need ", "how do i ", "how to "):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break

    if len(text) <= max_len:
        title = text
    else:
        cut = text[:max_len]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        title = cut + "…"

    return f"{DEFAULT_TAB_PREFIX}: {title}"


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
    available = {entry["name"] for entry in list_ollama_models()}
    if model in available:
        return model
    # Ollama tags often omit :latest
    if f"{model}:latest" in available:
        return f"{model}:latest"
    for name in available:
        if name.split(":")[0] == model.split(":")[0]:
            return name
    raise ValueError(
        f"Model '{model}' is not available locally. "
        f"Run: ollama pull {model.split(':')[0]}"
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
        raise RuntimeError(
            "No Ollama models found. Pull one first, e.g.: ollama pull llama3.2:3b"
        )

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


def print_banner(model: str, embedding_model: str) -> None:
    if not RICH_AVAILABLE:
        print(f"Claw Coder — model: {model} | embeddings: {embedding_model}")
        return

    body = Text()
    body.append("Claw Coder\n", style="bold cyan")
    body.append(f"chat  {model}\n", style="white")
    body.append(f"embed {embedding_model}\n", style="dim")
    body.append("\nCommands: /models  /pdf <file>  /title  exit\n", style="dim italic")
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


def print_error(message: str) -> None:
    if RICH_AVAILABLE:
        _console().print(f"[bold red]Error:[/bold red] {message}")
    else:
        print(f"Error: {message}")


def print_goodbye() -> None:
    set_terminal_title(DEFAULT_TAB_PREFIX)
    if RICH_AVAILABLE:
        _console().print("\n[dim]Goodbye — run `claw chat` anytime.[/dim]\n")
    else:
        print("\nGoodbye — run `claw chat` anytime.\n")


class ChatSpinner:
    """Show activity while the agent thinks."""

    def __init__(self, label: str = "Thinking…") -> None:
        self.label = label
        self._status: Optional[Status] = None

    def __enter__(self) -> "ChatSpinner":
        if RICH_AVAILABLE:
            self._status = _console().status(f"[cyan]{self.label}[/cyan]", spinner="dots")
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
