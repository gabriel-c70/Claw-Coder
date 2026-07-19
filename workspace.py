"""
Optional remote workspace support for Claw-Coder.

Local mode stays the default. When a user opts into workspace mode, Claw keeps
the terminal UI and auth local while chat, model pulls, and coding tools run on
the user's Codespace over SSH.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse


SSH_CONFIG_PATH = Path.home() / ".ssh" / "config"
SSH_MARKER_START = "# >>> claw-coder workspace >>>"
SSH_MARKER_END = "# <<< claw-coder workspace <<<"
DEFAULT_REMOTE_DIR = "/workspaces"
REMOTE_AGENT_DIR = "~/.claw-coder/remote-agent"

REMOTE_TOOL_SCRIPT = r"""
import base64
import json
import os
import sys
from pathlib import Path

payload = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
workspace_dir = Path(payload.get("workspace_dir") or ".").expanduser()
if workspace_dir.exists():
    os.chdir(workspace_dir)

sys.path.insert(0, str(Path(payload["agent_dir"]).expanduser()))
from agent_rag import Agent

agent = Agent(
    model=payload.get("model") or os.getenv("CLAW_MODEL") or os.getenv("OLLAMA_MODEL") or "llama3.2:1b",
    embedding_model=payload.get("embedding_model") or os.getenv("CLAW_EMBEDDING_MODEL", "qwen3-embedding:4b"),
    workspace_mode="local",
)
print(agent.execute_tool(payload["tool_name"], payload.get("tool_input") or {}))
"""

REMOTE_CHAT_SCRIPT = r"""
import base64
import json
import os
import sys
from pathlib import Path

payload = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
workspace_dir = Path(payload.get("workspace_dir") or ".").expanduser()
if workspace_dir.exists():
    os.chdir(workspace_dir)

import ollama

response = ollama.chat(
    model=payload["model"],
    messages=payload["messages"],
    tools=payload.get("tools"),
    stream=False,
)
print(json.dumps(response, ensure_ascii=False))
"""


@dataclass
class WorkspaceConfig:
    mode: str = "local"
    ssh_target: Optional[str] = None
    remote_dir: str = DEFAULT_REMOTE_DIR
    remote_agent_dir: str = REMOTE_AGENT_DIR
    python: str = "python3"
    timeout_seconds: int = 14400


class WorkspaceRemoteClient:
    REMOTE_TOOLS: Set[str] = {
        "read_files",
        "list_files",
        "edit_file",
        "create_file",
        "delete_file",
        "apply_patch",
        "git_apply_patch",
        "gnu_patch",
        "git_diff",
        "git_status",
        "extract_functions",
        "search_code",
        "run_terminal",
        "run_tests",
        "execute_code_in_docker",
        "ingest_code_knowledge",
        "ingest_pdf_knowledge",
        "ingest_paths_knowledge",
        "search_knowledge_base",
        "search_knowledge_graph",
    }

    def __init__(self, config: WorkspaceConfig) -> None:
        self.config = config

    @property
    def active(self) -> bool:
        return self.config.mode == "ssh" and bool(self.config.ssh_target)

    def should_delegate(self, tool_name: str) -> bool:
        return self.active and tool_name in self.REMOTE_TOOLS

    def status(self) -> str:
        if not self.active:
            return "Workspace mode: local. Run /workspace and paste your Codespace SSH link to move work remote."
        return (
            "Workspace mode: Claw-Coder's new machine\n"
            f"Host: {self.config.ssh_target}\n"
            f"Workspace: {self.config.remote_dir}\n"
            "Chat, model pulls, and coding tools are running remotely."
        )

    def setup_from_paste(self, pasted: str, package_root: Path, on_status: Optional[Any] = None) -> str:
        def status(msg: str) -> None:
            if on_status:
                on_status(msg)

        target = parse_codespace_target(pasted)
        if not target:
            return (
                "Could not find an SSH host in that input.\n"
                "Supported formats:\n"
                "  - SSH command: ssh user@hostname\n"
                "  - Simple hostname: hostname or user@hostname\n"
                "  - With port: hostname:port or user@hostname:port\n"
                "  - IP address: 192.168.1.1 or user@192.168.1.1\n"
                "  - GitHub Codespaces: https://github.com/codespaces/...\n"
                "  - SSH config alias: your-alias-from-ssh-config\n"

            )

        status(f"Configuring SSH for {target}...")
        config_message = self.ensure_ssh_config(target)

        status("Checking on the connection...")
        verify = self._ssh(target, "printf claw-workspace-ready", timeout=300)
        if verify.returncode != 0:
            error_msg = (verify.stderr or verify.stdout).strip()
            return (
                f"SSH config step: {config_message}\n"
                f"Could not connect to {target}.\n"
                f"Error: {error_msg}\n\n"
                f"Troubleshooting:\n"
                f"  1. Ensure the host is reachable: ssh {target}\n"
                f"  2. Check your SSH config: cat ~/.ssh/config\n"
                f"  3. Verify authentication: ssh -v {target}\n"
                f"  4. For GPU servers, ensure SSH is running and port is open\n"
            )

        self.config.mode = "ssh"
        self.config.ssh_target = target

        status("Locating your project directory...")
        discovered = self.discover_workspace_dir(target)
        if discovered:
            self.config.remote_dir = discovered
            status(f"Found workspace directory: {discovered}")
        else:
            status(f"Using default directory: {self.config.remote_dir}")

        status("Syncing Claw-Coder to the remote...")
        copied = self.sync_backend(package_root)

        status("Installing dependencies and setting up Ollama...")
        prepared = self.prepare_remote(on_status=status)

        return (
            f"✅ Connected to {target}\n"
            f"SSH config: {config_message}\n"
            f"Remote workspace: {self.config.remote_dir}\n"
            f"Backend: {copied}\n"
            f"Dependencies: {prepared}\n"
            "Workspace mode is now active."
        )



    def ensure_ssh_config(self, target: str) -> str:
        if self.host_in_ssh_config(target):
            return "already configured in SSH config"
        
        # Handle GitHub Codespaces specifically
        if target.startswith("cs."):
            codespace = target[3:]
            try:
                gh = subprocess.run(
                    ["gh", "codespace", "ssh", "--config", "--codespace", codespace],
                    text=True,
                    capture_output=True,
                    timeout=14400,
                )
            except FileNotFoundError:
                return "GitHub CLI not found; install gh or configure SSH manually for codespaces"
            if gh.returncode == 0 and gh.stdout.strip():
                self.write_managed_ssh_config(gh.stdout.strip())
                return "configured with gh codespace ssh --config"
            return (gh.stderr or gh.stdout or "gh codespace ssh --config returned no config").strip()
        
        # For non-codespace targets, create a basic SSH config entry
        # This is a minimal config that will use the user's existing SSH keys and defaults
        basic_config = f"""Host {target}
    UserKnownHostsFile ~/.ssh/known_hosts
    StrictHostKeyChecking accept-new"""
        
        try:
            self.write_managed_ssh_config(basic_config)
            return f"added basic SSH config for {target}"
        except Exception as e:
            return f"could not create SSH config: {str(e)}; will use SSH defaults"

    def pull_model(self, model: str) -> str:
        model = normalize_model_name(model)
        if not model:
            return "Missing model name."
        if any(char.isspace() for char in model):
            return "Invalid model name. Use names like llama3.2:1b or qwen2.5-coder:7b without spaces."
        if not self.active:
            return "Workspace is not connected. Run /workspace first."
        result = self._ssh(self.config.ssh_target or "", f"ollama pull {shlex.quote(model)}", timeout=self.config.timeout_seconds)
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return output or f"Could not pull {model} on {self.config.ssh_target}."
        return output or f"{model} installed on {self.config.ssh_target}."

    def list_models(self) -> List[Dict[str, Any]]:
        if not self.active:
            return []
        script = "import json, ollama; print(json.dumps(ollama.list(), default=lambda o: getattr(o, '__dict__', str(o))))"
        result = self._ssh(self.config.ssh_target or "", f"{self.config.python} -c {shlex.quote(script)}", timeout=35)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            return []
        raw_models = data.get("models", []) if isinstance(data, dict) else []
        models: List[Dict[str, Any]] = []
        for item in raw_models:
            if isinstance(item, dict):
                name = item.get("model") or item.get("name")
                size = item.get("size")
            else:
                name = getattr(item, "model", None) or getattr(item, "name", None)
                size = getattr(item, "size", None)
            if name:
                models.append({"name": name, "size": size})
        return sorted(models, key=lambda entry: entry["name"])

    def chat(self, model: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.active:
            raise RuntimeError("Workspace is not connected")
        model = normalize_model_name(model)
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "workspace_dir": self.config.remote_dir,
        }
        output = self._remote_python(REMOTE_CHAT_SCRIPT, payload)
        return json.loads(output)

    def execute_tool(self, tool_name: str, tool_input: Dict[str, Any], model: str = "", embedding_model: str = "") -> str:
        if not self.active:
            return json.dumps({"status": "error", "error": "Workspace is not connected"})
        if tool_name == "run_terminal":
            return self._run_terminal(tool_input)
        payload = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "workspace_dir": self.config.remote_dir,
            "agent_dir": self.config.remote_agent_dir,
            "model": model,
            "embedding_model": embedding_model,
        }
        return self._remote_python(REMOTE_TOOL_SCRIPT, payload)

    def sync_backend(self, package_root: Path) -> str:
        files = [
            "agent_rag.py",
            "agent_knowledge.py",
            "claw_ui.py",
            "workspace.py",
            "requirements.txt",
            "claw_coder_system_prompt",
        ]
        existing = [name for name in files if (package_root / name).exists()]
        if not existing:
            return "no local backend files found"
        remote_dir = self._remote_shell_path(self.config.remote_agent_dir)
        tar_cmd = "tar -czf - " + " ".join(shlex.quote(name) for name in existing)
        remote_cmd = f"mkdir -p {remote_dir} && tar -xzf - -C {remote_dir}"
        result = subprocess.run(
            f"{tar_cmd} | ssh {shlex.quote(self.config.ssh_target or '')} {shlex.quote(remote_cmd)}",
            cwd=package_root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=14400,
        )
        if result.returncode != 0:
            return (result.stderr or result.stdout or "copy failed").strip()
        return f"synced to {self.config.remote_agent_dir}"

    def prepare_remote(self, on_status: Optional[Any] = None) -> str:
        def status(msg: str) -> None:
            if on_status:
                on_status(msg)

        target = self.config.ssh_target or ""
        remote_agent_dir = self._remote_shell_path(self.config.remote_agent_dir)

        # 1. Python deps
        status("Installing Python dependencies on the remote...")
        pip_cmd = (
            f"cd {remote_agent_dir} && "
            f"{shlex.quote(self.config.python)} -m pip install --user -r requirements.txt "
            f">/tmp/claw-coder-pip.log 2>&1"
        )
        pip_result = self._ssh(target, pip_cmd, timeout=300)
        pip_status = "ok" if pip_result.returncode == 0 else "pip install failed, see /tmp/claw-coder-pip.log on remote"

        # 2. Ollama binary
        has_ollama = self._ssh(target, "command -v ollama >/dev/null && printf yes || printf no", timeout=15)
        if has_ollama.stdout.strip() != "yes":
            status("Installing Ollama on the remote...")
            install = self._ssh(target, "curl -fsSL https://ollama.com/install.sh | sh", timeout=180)
            if install.returncode != 0:
                return f"deps: {pip_status}; ollama install failed: {(install.stderr or install.stdout)[-500:].strip()}"

        # 3. Ollama daemon running?
        is_running = self._ssh(target, "ollama list >/dev/null 2>&1 && printf yes || printf no", timeout=15)
        if is_running.stdout.strip() != "yes":
            status("Starting ollama serve on the remote...")
            self._ssh(target, "nohup ollama serve > /tmp/ollama.log 2>&1 & sleep 3; printf started", timeout=20)

        return f"deps: {pip_status}; ollama: installed and running"

    def discover_workspace_dir(self, target: str) -> Optional[str]:
        """
        Tries several conventions in order, since not every remote follows
        Codespaces' /workspaces/<repo> layout — keeping this provider-agnostic
        now means adding RunPod (or anything else) later doesn't require
        rewriting this logic, just adding another candidate root.
        """
        candidate_roots = [
            "/workspaces",  # GitHub Codespaces convention
            "/workspace",  # common in many cloud dev-container / GPU-pod images (singular)
            "/root",  # common default working dir on bare GPU pods (e.g. RunPod)
            "/home",  # common on Linux systems
            "$HOME",  # universal fallback
            "/mycodeenvironment",
            "/app",  # common in containerized applications
            "/project",  # another common project directory
        ]

        for root in candidate_roots:
            # Expand $HOME if needed
            if root == "$HOME":
                home_check = self._ssh(target, "echo $HOME", timeout=15)
                if home_check.returncode == 0:
                    root = home_check.stdout.strip()
                else:
                    continue
            
            # Check if the root exists
            check_cmd = f"test -d {shlex.quote(root)} && echo 'exists' || echo 'notfound'"
            check_result = self._ssh(target, check_cmd, timeout=100)
            if check_result.returncode != 0 or "notfound" in check_result.stdout:
                continue
            
            command = f"find {shlex.quote(root)} -mindepth 1 -maxdepth 1 -type d 2>/dev/null"
            result = self._ssh(target, command, timeout=100)
            if result.returncode != 0:
                continue
            candidates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if not candidates:
                continue

            if len(candidates) == 1:
                return candidates[0]

            # Multiple candidates under this root — prefer the one that's a real git repo.
            git_check = self._ssh(
                target,
                " ; ".join(
                    f"test -d {shlex.quote(c + '/.git')} && printf '%s\\n' {shlex.quote(c)}" for c in candidates),
                timeout=100,
            )
            git_dirs = [line.strip() for line in git_check.stdout.splitlines() if line.strip()]
            if len(git_dirs) == 1:
                return git_dirs[0]

            # Still ambiguous — Codespaces sets $RepositoryName; use it if it matches.
            repo_env = self._ssh(target, "echo $RepositoryName", timeout=100)
            repo_name = repo_env.stdout.strip()
            if repo_name:
                for c in candidates:
                    if c.rsplit("/", 1)[-1] == repo_name:
                        return c

            # Check for common project indicators (package.json, requirements.txt, etc.)
            project_check = self._ssh(
                target,
                " ; ".join(
                    f"test -f {shlex.quote(c + '/package.json')} -o test -f {shlex.quote(c + '/requirements.txt')} -o test -f {shlex.quote(c + '/setup.py')} && printf '%s\\n' {shlex.quote(c)}" for c in candidates),
                timeout=100,
            )
            project_dirs = [line.strip() for line in project_check.stdout.splitlines() if line.strip()]
            if len(project_dirs) == 1:
                return project_dirs[0]

            # Deterministic fallback rather than an arbitrary `head -n 1` pick.
            return sorted(candidates)[0]

        # Nothing matched any known convention — caller falls back to $HOME itself
        # or asks the user for an explicit path.
        home_check = self._ssh(target, "echo $HOME", timeout=100)
        home_dir = home_check.stdout.strip()
        return home_dir or None

    def _run_terminal(self, tool_input: Dict[str, Any]) -> str:
        command = str(tool_input.get("command", "")).strip()
        if not command:
            return json.dumps({"status": "error", "error": "Missing command"})
        timeout = int(tool_input.get("timeout", 35))
        remote_command = f"cd {shlex.quote(self.config.remote_dir)} && {command}"
        try:
            result = self._ssh(self.config.ssh_target or "", remote_command, timeout=max(1, timeout))
            return json.dumps(
                {
                    "status": "ok" if result.returncode == 0 else "error",
                    "workspace": "ssh",
                    "host": self.config.ssh_target,
                    "result": {
                        "command": command,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "returncode": result.returncode,
                    },
                },
                ensure_ascii=False,
            )
        except subprocess.TimeoutExpired as exc:
            return json.dumps(
                {
                    "status": "error",
                    "workspace": "ssh",
                    "host": self.config.ssh_target,
                    "result": {
                        "command": command,
                        "stdout": exc.stdout or "",
                        "stderr": f"Command timed out after {timeout} seconds.",
                        "returncode": 124,
                    },
                },
                ensure_ascii=False,
            )

    @staticmethod
    def _ssh(target: str, command: str, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["ssh", target, command],
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    @staticmethod
    def _remote_shell_path(path: str) -> str:
        if path.startswith("~/"):
            return "$HOME/" + shlex.quote(path[2:])
        return shlex.quote(path)

    @staticmethod
    def host_in_ssh_config(target: str) -> bool:
        if not SSH_CONFIG_PATH.exists():
            return False
        text = SSH_CONFIG_PATH.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0].lower() == "host" and target in parts[1:]:
                return True
        return False

    @staticmethod
    def write_managed_ssh_config(config_text: str) -> None:
        SSH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = SSH_CONFIG_PATH.read_text(encoding="utf-8") if SSH_CONFIG_PATH.exists() else ""
        pattern = re.compile(
            rf"\n?{re.escape(SSH_MARKER_START)}.*?{re.escape(SSH_MARKER_END)}\n?",
            re.DOTALL,
        )
        cleaned = pattern.sub("\n", existing).rstrip()
        block = f"{SSH_MARKER_START}\n{config_text.strip()}\n{SSH_MARKER_END}\n"
        SSH_CONFIG_PATH.write_text((cleaned + "\n\n" + block).lstrip(), encoding="utf-8")
        try:
            os.chmod(SSH_CONFIG_PATH, 0o600)
        except OSError:
            pass


def normalize_model_name(model: str) -> str:
    return " ".join(str(model).strip().split())


def parse_codespace_target(value: str) -> Optional[str]:
    """
    Parse various SSH target formats and return a clean hostname.
    
    Supports:
    - SSH commands: "ssh user@hostname", "ssh hostname"
    - GitHub Codespaces URLs: "https://github.com/codespaces/..."
    - Simple hostnames: "hostname", "user@hostname"
    - Hostnames with port: "hostname:port", "user@hostname:port"
    - IP addresses: "192.168.1.1", "user@192.168.1.1"
    """
    text = value.strip()
    if not text:
        return None
    
    # Validate input doesn't contain spaces (except in ssh command format)
    if " " in text and not text.startswith("ssh "):
        return None
    
    # Handle SSH command format
    if text.startswith("ssh "):
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        for part in reversed(parts[1:]):
            if not part.startswith("-") and "=" not in part:
                # Recursively parse the extracted part to handle user@host:port format
                return parse_codespace_target(part)
    
    # Handle URL format (GitHub Codespaces, etc.)
    parsed = urlparse(text)
    if parsed.scheme:
        query = parse_qs(parsed.query)
        name = (query.get("name") or query.get("codespace") or [None])[0]
        if name:
            return name if name.startswith("cs.") else f"cs.{name}"
        path_name = parsed.path.rstrip("/").split("/")[-1]
        if path_name:
            return path_name if path_name.startswith("cs.") else f"cs.{path_name}"
    
    # Handle various SSH target formats
    # Remove ssh:// prefix if present
    if text.startswith("ssh://"):
        text = text[6:]
    
    # Extract hostname from user@host:port format
    # This handles: user@hostname, user@hostname:port, hostname:port, hostname
    ssh_target = text
    
    # Remove port specification if present
    if ":" in ssh_target and not ssh_target.startswith("["):
        # Handle IPv6 addresses in brackets [::1]:port
        if ssh_target.startswith("["):
            bracket_end = ssh_target.find("]")
            if bracket_end != -1:
                ssh_target = ssh_target[:bracket_end + 1]
        else:
            ssh_target = ssh_target.split(":")[0]
    
    # Remove user@ prefix if present
    if "@" in ssh_target:
        ssh_target = ssh_target.split("@")[1]
    
    # Basic validation - allow alphanumeric, dots, dashes, and underscores
    # Also allow IPv6 addresses with colons and brackets
    if re.match(r"^[A-Za-z0-9_.-]+$", ssh_target):
        return ssh_target
    elif re.match(r"^\[?[A-Za-z0-9:.]+\]?$", ssh_target):
        return ssh_target
    
    # If nothing matched, check if it could be a valid SSH config alias
    # SSH aliases can contain alphanumeric, dots, dashes, underscores
    if re.match(r"^[A-Za-z0-9_.-]+$", text):
        return text
    
    # If still nothing matched, return None to indicate invalid input
    return None
