"""Undo support: snapshots of files the agent writes, grouped by user turn.

Each session has `<state_dir>/snapshots/<session>/changes.jsonl`; one line per successful write_file/edit_file:
`{"turn", "path", "before" (base64 or null if the file did not exist), "after_sha256"}`.
Changes made through run_command are not tracked.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from coding_agent.services.workspace import Workspace

MAX_SNAPSHOT_BYTES = 5_000_000


def sha256_of(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


@dataclass
class Snapshot:
    """Content of a file captured before the agent changed it."""

    path: str
    before: bytes | None
    restorable: bool = True


@dataclass
class UndoResult:
    turn: str | None = None
    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def changed(self) -> list[str]:
        return [*self.restored, *self.deleted]


class FileHistory:
    def __init__(self, state_dir: Path, session_id: str, workspace: Workspace):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:128]
        self.file = state_dir / "snapshots" / safe / "changes.jsonl"
        self.workspace = workspace

    def capture(self, path: str) -> Snapshot | None:
        """Read a file's current content before a write. Returns None if the path is invalid."""
        try:
            resolved = self.workspace.resolve(path)
        except Exception:
            return None
        rel = self.workspace.display(resolved)
        if not resolved.exists():
            return Snapshot(rel, None)
        if not resolved.is_file() or resolved.stat().st_size > MAX_SNAPSHOT_BYTES:
            return Snapshot(rel, None, restorable=False)
        return Snapshot(rel, resolved.read_bytes())

    def record(self, turn: str, snapshot: Snapshot) -> None:
        """Append a change after the write succeeded."""
        after = sha256_of(self.workspace.resolve(snapshot.path))
        entry = {
            "turn": turn,
            "path": snapshot.path,
            "before": None if snapshot.before is None else base64.b64encode(snapshot.before).decode(),
            "restorable": snapshot.restorable,
            "after_sha256": after,
        }
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with self.file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def entries(self) -> list[dict]:
        if not self.file.exists():
            return []
        return [json.loads(line) for line in self.file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def undo_last_turn(self) -> UndoResult:
        entries = self.entries()
        if not entries:
            return UndoResult()
        turn = entries[-1]["turn"]
        undo = [e for e in entries if e["turn"] == turn]
        result = UndoResult(turn=turn)
        for entry in reversed(undo):
            rel = entry["path"]
            path = self.workspace.resolve(rel)
            if not entry.get("restorable", True) or sha256_of(path) != entry["after_sha256"]:
                if rel not in result.conflicts:
                    result.conflicts.append(rel)
                continue
            if entry["before"] is None:
                path.unlink()
                result.deleted.append(rel)
            else:
                path.write_bytes(base64.b64decode(entry["before"]))
                if rel not in result.restored:
                    result.restored.append(rel)
        # A file restored by one entry and deleted by an earlier one (created then edited) is reported once.
        result.restored = [p for p in result.restored if p not in result.deleted]
        remaining = [e for e in entries if e["turn"] != turn]
        self.file.write_text("".join(json.dumps(e) + "\n" for e in remaining), encoding="utf-8")
        return result
