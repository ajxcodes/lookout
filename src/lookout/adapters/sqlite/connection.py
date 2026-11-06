"""SQLite connection manager with WAL mode, foreign keys, and safe read/write separation."""

import asyncio
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from lookout.adapters.sqlite.schema import initialize_database

T = TypeVar("T")


class SqliteConnectionManager:
    """Manages SQLite connections, WAL mode, and concurrency-safe read/write separation."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        wal_mode: bool = True,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.db_path = str(db_path)
        self.wal_mode = wal_mode
        self.busy_timeout_ms = busy_timeout_ms
        self._write_lock = asyncio.Lock()
        self._is_memory = self.db_path == ":memory:"

        if self._is_memory:
            # Use unique shared memory URI so multiple connections can access the same DB
            self._connect_target = f"file:lookout_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._use_uri = True
        else:
            p = Path(self.db_path)
            if p.parent and not p.parent.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
            self._connect_target = str(p.resolve())
            self._use_uri = False

        # Open dedicated write connection
        self._write_conn = self._create_connection()
        # Initialize schema and pragmas
        initialize_database(
            self._write_conn, wal_mode=self.wal_mode, busy_timeout_ms=self.busy_timeout_ms
        )

    def _create_connection(self) -> sqlite3.Connection:
        """Create and configure a new SQLite connection."""
        conn = sqlite3.connect(
            self._connect_target,
            uri=self._use_uri,
            check_same_thread=False,
            timeout=self.busy_timeout_ms / 1000.0,
        )
        conn.row_factory = sqlite3.Row
        if self.wal_mode and not self._is_memory:
            conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    async def execute_read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute a read-only query in a worker thread with an isolated read connection."""

        def _read_worker() -> T:
            conn = self._create_connection()
            try:
                return fn(conn)
            finally:
                conn.close()

        return await asyncio.to_thread(_read_worker)

    async def execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute a write transaction serialized by an asyncio lock."""
        async with self._write_lock:

            def _write_worker() -> T:
                try:
                    res = fn(self._write_conn)
                    self._write_conn.commit()
                    return res
                except Exception:
                    self._write_conn.rollback()
                    raise

            return await asyncio.to_thread(_write_worker)

    def close(self) -> None:
        """Close the write connection."""
        if self._write_conn:
            self._write_conn.close()
            self._write_conn = None  # type: ignore[assignment]

    def __del__(self) -> None:
        """Ensure connection is closed upon garbage collection."""
        self.close()
