"""Structured audit logging for security, operations, and retention events."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lookout.adapters.sqlite.connection import SqliteConnectionManager


class AuditLogger:
    """Structured audit logger recording events into the SQLite audit_log table."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        connection_manager: SqliteConnectionManager | None = None,
    ) -> None:
        self._manager = connection_manager or SqliteConnectionManager(db_path=db_path)

    async def log(
        self,
        action: str,
        entity_type: str,
        entity_id: str | None = None,
        details: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        """Insert structured audit entry; returns audit entry ID."""
        audit_id = str(uuid.uuid4())
        ts = timestamp or datetime.now(UTC)
        details_payload = json.dumps(details or {})

        def _write(conn: sqlite3.Connection) -> str:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_log (id, timestamp, action, entity_type, entity_id, details_json)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (audit_id, ts.isoformat(), action, entity_type, entity_id, details_payload),
            )
            return audit_id

        return await self._manager.execute_write(_write)

    async def query(
        self,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query audit log entries matching criteria ordered newest first."""

        def _read(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            cursor = conn.cursor()
            conditions: list[str] = []
            params: list[Any] = []

            if action is not None:
                conditions.append("action = ?")
                params.append(action)
            if entity_type is not None:
                conditions.append("entity_type = ?")
                params.append(entity_type)
            if entity_id is not None:
                conditions.append("entity_id = ?")
                params.append(entity_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = (
                f"SELECT * FROM audit_log {where_clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?;"
            )
            params.extend([limit, offset])

            cursor.execute(sql, params)
            rows = cursor.fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                try:
                    item["details"] = json.loads(row["details_json"])
                except Exception:
                    item["details"] = {}
                results.append(item)
            return results

        return await self._manager.execute_read(_read)
