"""Filesystem tools: list, read, search, write and edit files inside the workspace."""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from coding_agent.config.settings import Settings
from coding_agent.services.workspace import Workspace
from coding_agent.utils.security import is_binary, truncate

MAX_LIST_ENTRIES = 500
MAX_SEARCH_MATCHES = 200
FILE_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})


class ToolError(Exception):
    """A recoverable tool failure; the message is shown to the model."""


def read_text(path: Path, max_bytes: int) -> str:
    if not path.exists():
        raise ToolError(f"File not found: {path.name}")
    if path.is_dir():
        raise ToolError(f"{path.name} is a directory; use list_files instead")
    size = path.stat().st_size
    if size > max_bytes:
        raise ToolError(f"File is {size} bytes, above the {max_bytes} byte limit; read a line range instead")
    data = path.read_bytes()
    if is_binary(data):
        raise ToolError(f"{path.name} is a binary file")
    return data.decode("utf-8", errors="replace")


def unified_diff(path_label: str, before: str, after: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path_label}",
        tofile=f"b/{path_label}",
    )
    text = "".join(line if line.endswith("\n") else line + "\n" for line in diff)
    return text or "(no changes)"


def apply_edit(original: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    if not old_string:
        raise ToolError("old_string must not be empty; use write_file to create files")
    count = original.count(old_string)
    if count == 0:
        raise ToolError("old_string was not found in the file; read the file and copy the text exactly")
    if count > 1 and not replace_all:
        raise ToolError(f"old_string occurs {count} times; add surrounding context or set replace_all")
    return original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)


def walk_files(workspace: Workspace, start: Path):
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if not workspace.is_ignored_dir(d))
        for name in sorted(filenames):
            yield Path(dirpath) / name


def make_filesystem_tools(settings: Settings, workspace: Workspace) -> list[BaseTool]:
    @tool
    def list_files(path: str = ".", max_depth: int = 2) -> str:
        """List files and directories under `path` (relative to the workspace), up to `max_depth` levels.

        Directories end with '/'. Dependency and cache directories (node_modules, .venv, .git, ...) are skipped.
        """
        root = workspace.resolve(path)
        if not root.exists():
            raise ToolError(f"Path not found: {path}")
        if root.is_file():
            return workspace.display(root)
        lines: list[str] = []
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            depth = len(current.parts) - base_depth
            dirnames[:] = sorted(d for d in dirnames if not workspace.is_ignored_dir(d))
            indent = "  " * depth
            if depth > 0:
                lines.append(f"{'  ' * (depth - 1)}{current.name}/")
            if depth >= max_depth:
                if dirnames or filenames:
                    lines.append(f"{indent}...")
                dirnames[:] = []
                continue
            lines.extend(f"{indent}{name}" for name in sorted(filenames))
            if len(lines) >= MAX_LIST_ENTRIES:
                lines.append(f"... (listing truncated at {MAX_LIST_ENTRIES} entries)")
                break
        header = f"{workspace.display(root)}/"
        return "\n".join([header, *lines]) if lines else f"{header} (empty)"

    @tool
    def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Read a text file from the workspace. Lines are prefixed with their line number.

        Use start_line/end_line (1-based, inclusive) to read part of a large file.
        """
        resolved = workspace.resolve_readable(path)
        if not resolved.exists():
            raise ToolError(f"File not found: {path}")
        if resolved.is_dir():
            raise ToolError(f"{path} is a directory; use list_files instead")
        ranged = start_line > 1 or end_line is not None
        if ranged:
            with resolved.open("rb") as fh:
                if is_binary(fh.read(8192)):
                    raise ToolError(f"{path} is a binary file")
            text = resolved.read_text(encoding="utf-8", errors="replace")
        else:
            text = read_text(resolved, settings.max_file_bytes)
        lines = text.splitlines()
        start = max(start_line, 1)
        end = len(lines) if end_line is None else min(end_line, len(lines))
        if not lines:
            return f"{path} is empty"
        if start > len(lines):
            raise ToolError(f"start_line {start} is past the end of the file ({len(lines)} lines)")
        width = len(str(end))
        body = "\n".join(f"{n:>{width}}  {lines[n - 1]}" for n in range(start, end + 1))
        return truncate(body, settings.max_tool_output)

    @tool
    def search_files(pattern: str, path: str = ".", glob: str | None = None, ignore_case: bool = False) -> str:
        """Search file contents for a regular expression. Returns `file:line: text` matches.

        `glob` optionally filters file names, e.g. "*.py".
        """
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ToolError(f"Invalid regular expression: {exc}") from exc
        root = workspace.resolve(path)
        files = [root] if root.is_file() else walk_files(workspace, root)
        matches: list[str] = []
        for file in files:
            if glob and not fnmatch.fnmatch(file.name, glob):
                continue
            if workspace.is_secret(file):
                continue
            try:
                if file.stat().st_size > settings.max_file_bytes:
                    continue
                data = file.read_bytes()
            except OSError:
                continue
            if is_binary(data):
                continue
            for lineno, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{workspace.display(file)}:{lineno}: {line.strip()[:300]}")
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        matches.append(f"... (stopped after {MAX_SEARCH_MATCHES} matches)")
                        return "\n".join(matches)
        return "\n".join(matches) if matches else "No matches."

    @tool
    def write_file(path: str, content: str) -> str:
        """Create a file or overwrite it entirely with `content`. Prefer edit_file for changes to existing files."""
        resolved = workspace.resolve(path)
        if resolved.is_dir():
            raise ToolError(f"{path} is a directory")
        existed = resolved.exists()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        verb = "Updated" if existed else "Created"
        return f"{verb} {workspace.display(resolved)} ({len(content.splitlines())} lines)"

    @tool
    def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """Replace an exact snippet in a file. `old_string` must match exactly once unless replace_all is set."""
        resolved = workspace.resolve_readable(path)
        original = read_text(resolved, settings.max_file_bytes)
        updated = apply_edit(original, old_string, new_string, replace_all)
        resolved.write_text(updated, encoding="utf-8")
        return f"Edited {workspace.display(resolved)}"

    return [list_files, read_file, search_files, write_file, edit_file]


def preview_file_change(settings: Settings, workspace: Workspace, name: str, args: dict) -> str | None:
    """Compute the diff a write_file/edit_file call would produce, for the approval prompt."""
    try:
        resolved = workspace.resolve(args.get("path", ""))
        label = workspace.display(resolved)
        before = read_text(resolved, settings.max_file_bytes) if resolved.exists() else ""
        if name == "write_file":
            after = args.get("content", "")
        else:
            after = apply_edit(before, args.get("old_string", ""), args.get("new_string", ""), args.get("replace_all", False))
    except Exception as exc:  # the tool itself will report the failure on execution
        return f"(diff unavailable: {exc})"
    return unified_diff(label, before, after)
