"""Policy layer: decide whether a tool call is safe, needs human approval, or is denied outright."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from coding_agent.config.settings import Settings
from coding_agent.services.workspace import Workspace
from coding_agent.tools.filesystem import FILE_WRITE_TOOL_NAMES
from coding_agent.utils.security import SecurityError, is_secret_path


class Risk(StrEnum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    HIGH = "high"
    DENIED = "denied"


@dataclass(frozen=True)
class PolicyDecision:
    risk: Risk
    reason: str = ""

    @property
    def denied(self) -> bool:
        return self.risk is Risk.DENIED


READ_ONLY_TOOLS = {"list_files", "read_file", "search_files", "git_status", "git_diff", "git_log", "git_show"}
# Tools that only change the agent's own state (never the workspace).
AGENT_STATE_TOOLS = {"remember"}
GIT_WRITE_TOOLS = {"git_add", "git_commit"}

DENY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|[;&|\s])sudo\s"), "privilege escalation (sudo)"),
    (re.compile(r"(^|[;&|\s])su(\s|$)"), "privilege escalation (su)"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "formats a filesystem"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "writes to a raw device"),
    (re.compile(r">\s*/dev/(sd|nvme|hd|disk)"), "writes to a raw device"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "shuts down the machine"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "fork bomb"),
    (re.compile(r"\brm\s+(-[^\s]*\s+)*(/|/\*|~|~/|\$HOME/?|\*)(\s|$)"), "deletes the root, home or entire directory"),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b"), "pipes a remote script into a shell"),
    (re.compile(r"\bchmod\s+(-R\s+)?[0-7]*777\s+/(\s|$)"), "opens permissions on the root filesystem"),
]

HIGH_RISK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+(.*\s)?-[a-zA-Z]*[rRf]"), "recursive or forced delete"),
    (re.compile(r"\bgit\s+reset\b"), "git reset can discard work"),
    (re.compile(r"\bgit\s+clean\b"), "git clean deletes untracked files"),
    (re.compile(r"\bgit\s+push\b.*(--force|-f\b|--force-with-lease)"), "force push rewrites remote history"),
    (re.compile(r"\bgit\s+push\b"), "publishes commits to a remote"),
    (re.compile(r"\bgit\s+(rebase|filter-branch|filter-repo)\b"), "rewrites history"),
    (re.compile(r"\bgit\s+commit\b.*--amend"), "rewrites the last commit"),
    (re.compile(r"\bgit\s+(checkout|restore)\s+.*(--\s|\.(\s|$))"), "discards working-tree changes"),
    (re.compile(r"\bgit\s+branch\s+-D\b"), "force-deletes a branch"),
    (re.compile(r"\bgit\s+stash\s+(drop|clear)\b"), "deletes stashed work"),
    (re.compile(r"\b(chmod|chown)\s+-R\b"), "recursive permission change"),
    (re.compile(r"\b(kill|pkill|killall)\b"), "kills processes"),
]

SAFE_COMMANDS: list[tuple[str, ...]] = [
    ("ls",),
    ("pwd",),
    ("cat",),
    ("head",),
    ("tail",),
    ("wc",),
    ("echo",),
    ("grep",),
    ("rg",),
    ("tree",),
    ("which",),
    ("file",),
    ("stat",),
    ("diff",),
    ("du",),
    ("sort",),
    ("uniq",),
    ("git", "status"),
    ("git", "diff"),
    ("git", "log"),
    ("git", "show"),
    ("git", "branch"),
    ("git", "rev-parse"),
    ("git", "blame"),
    ("git", "ls-files"),
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("uv", "run", "pytest"),
    ("uv", "run", "python", "-m", "pytest"),
    ("uv", "run", "ruff", "check"),
    ("uv", "run", "ruff", "format", "--check"),
    ("uv", "run", "mypy"),
    ("ruff", "check"),
    ("ruff", "format", "--check"),
    ("mypy",),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("npm", "run", "lint"),
    ("npm", "run", "typecheck"),
    ("bun", "test"),
    ("bun", "run", "test"),
    ("bun", "run", "lint"),
    ("bunx", "tsc", "--noEmit"),
    ("npx", "tsc", "--noEmit"),
    ("tsc", "--noEmit"),
    ("cargo", "test"),
    ("cargo", "check"),
    ("cargo", "clippy"),
    ("go", "test"),
    ("go", "vet"),
    ("go", "build"),
]
FIND_UNSAFE = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}
SHELL_METACHARS = re.compile(r"[;&|`<>\n]|\$\(")


class ApprovalPolicy:
    def __init__(self, settings: Settings, workspace: Workspace, trusted_tools: set[str] | None = None):
        self.settings = settings
        self.workspace = workspace
        self.trusted_tools = trusted_tools or set()

    def evaluate(self, name: str, args: dict) -> PolicyDecision:
        decision = self._classify(name, args)
        if decision.risk is Risk.SENSITIVE and not self.settings.require_approval:
            return PolicyDecision(Risk.SAFE, decision.reason)
        return decision

    @staticmethod
    def rule_key(name: str, args: dict) -> str:
        """Key for a session "always allow" rule: the exact command for run_command, the tool name otherwise."""
        if name == "run_command":
            return f"run_command:{str(args.get('command', '')).strip()}"
        return name

    def _classify(self, name: str, args: dict) -> PolicyDecision:
        if name in READ_ONLY_TOOLS or name in AGENT_STATE_TOOLS or name in self.trusted_tools:
            return PolicyDecision(Risk.SAFE)
        if name in FILE_WRITE_TOOL_NAMES:
            return self._classify_file_write(args.get("path", ""))
        if name in GIT_WRITE_TOOLS:
            return PolicyDecision(Risk.SENSITIVE, f"{name} modifies the git repository")
        if name == "git_branch":
            if args.get("create"):
                return PolicyDecision(Risk.SENSITIVE, "creates and switches to a new branch")
            return PolicyDecision(Risk.SAFE)
        if name == "run_command":
            return classify_command(str(args.get("command", "")), self.workspace)
        return PolicyDecision(Risk.SENSITIVE, f"{name} is an external tool")

    def _classify_file_write(self, path: str) -> PolicyDecision:
        try:
            resolved = self.workspace.resolve(path)
        except SecurityError as exc:
            return PolicyDecision(Risk.DENIED, str(exc))
        rel = self.workspace.display(resolved)
        if ".git" in Path(rel).parts:
            return PolicyDecision(Risk.DENIED, "writing inside .git is not allowed")
        if is_secret_path(resolved):
            return PolicyDecision(Risk.HIGH, f"{rel} looks like a secrets file")
        verb = "modifies" if resolved.exists() else "creates"
        return PolicyDecision(Risk.SENSITIVE, f"{verb} {rel}")


def classify_command(command: str, workspace: Workspace) -> PolicyDecision:
    stripped = command.strip()
    if not stripped:
        return PolicyDecision(Risk.DENIED, "empty command")
    for pattern, reason in DENY_PATTERNS:
        if pattern.search(stripped):
            return PolicyDecision(Risk.DENIED, f"blocked: {reason}")
    for pattern, reason in HIGH_RISK_PATTERNS:
        if pattern.search(stripped):
            return PolicyDecision(Risk.HIGH, reason)

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return PolicyDecision(Risk.SENSITIVE, "command could not be parsed")

    for token in tokens:
        if token.startswith("-"):
            continue
        if is_secret_path(Path(token)):
            return PolicyDecision(Risk.HIGH, f"references a secrets file ({token})")
        if token.startswith(("/", "~")) or ".." in Path(token).parts:
            try:
                workspace.resolve(token)
            except SecurityError:
                return PolicyDecision(Risk.SENSITIVE, f"references a path outside the workspace ({token})")

    without_redirect = stripped.replace("2>&1", "")
    if SHELL_METACHARS.search(without_redirect):
        return PolicyDecision(Risk.SENSITIVE, "uses shell operators (pipes, redirects, chaining)")
    if tokens and tokens[0] == "find" and not FIND_UNSAFE.intersection(tokens):
        return PolicyDecision(Risk.SAFE)
    if any(tuple(tokens[: len(prefix)]) == prefix for prefix in SAFE_COMMANDS):
        if tokens[:2] == ["git", "branch"] and len(tokens) > 2 and not tokens[2].startswith("-"):
            return PolicyDecision(Risk.SENSITIVE, "creates a branch")
        return PolicyDecision(Risk.SAFE)
    return PolicyDecision(Risk.SENSITIVE, "command is not on the read-only allowlist")
