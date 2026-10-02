"""Security primitives shared by tools: workspace confinement, secret detection, output limits."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path


class SecurityError(Exception):
    """Raised when a tool request violates a security rule."""


SECRET_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    ".netrc",
    ".pgpass",
    "credentials",
    "credentials.json",
    ".npmrc",
    ".pypirc",
)
SECRET_FILE_ALLOW = (".env.example", ".env.sample", ".env.template")

SECRET_ENV_PATTERN = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.I)
SAFE_ENV_NAMES = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR", "PWD", "XDG_CACHE_HOME"}


def resolve_in_workspace(workspace: Path, path: str | Path) -> Path:
    """Resolve `path` relative to `workspace`, refusing anything that escapes it (incl. via symlinks)."""
    root = workspace.resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise SecurityError(f"Path {str(path)!r} is outside the workspace {root}")
    return resolved


def relative_display(workspace: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return str(path)
    return str(rel) or "."


def is_secret_path(path: Path) -> bool:
    name = path.name
    if name in SECRET_FILE_ALLOW:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_FILE_PATTERNS)


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 3 // 4
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n... [{omitted} characters truncated] ...\n\n{text[-tail:]}"


def filtered_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for child processes with credentials removed."""
    source = os.environ if base is None else base
    return {k: v for k, v in source.items() if k in SAFE_ENV_NAMES or not SECRET_ENV_PATTERN.search(k)}
