"""Session index (id, workspace, title, timestamps) stored next to the checkpoints, for listing and resuming."""

from __future__ import annotations

import asyncio
import time

import aiosqlite

TITLE_CHARS = 80


class SessionStore:
    def __init__(self, conn: aiosqlite.Connection, lock: asyncio.Lock | None = None):
        self.conn = conn
        self.lock = lock or asyncio.Lock()

    async def setup(self) -> None:
        async with self.lock:
            await self.conn.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            await self.conn.execute("CREATE INDEX IF NOT EXISTS sessions_workspace ON sessions (workspace, updated_at)")
            await self.conn.commit()

    async def touch(self, session_id: str, workspace: str, message: str) -> None:
        """Create the session on its first message (titled after it) or bump its updated time."""
        now = time.time()
        title = " ".join(message.split())[:TITLE_CHARS]
        async with self.lock:
            await self.conn.execute(
                """INSERT INTO sessions (id, workspace, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at""",
                (session_id, workspace, title, now, now),
            )
            await self.conn.commit()

    async def list(self, workspace: str, limit: int = 20) -> list[dict]:
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT id, title, created_at, updated_at FROM sessions WHERE workspace = ? ORDER BY updated_at DESC LIMIT ?",
                (workspace, limit),
            )
            rows = await cursor.fetchall()
        return [{"session_id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]} for r in rows]
