"""Media retention worker enforcing tiered retention policies and file pruning."""

import asyncio
import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.domain.models import ThreatLevel


@dataclass
class RetentionResult:
    """Outcome of a media retention purge run."""

    events_purged: int = 0
    files_deleted: int = 0
    bytes_freed: int = 0


class MediaRetentionWorker:
    """Automated retention worker enforcing tiered retention and media cleanup."""

    def __init__(
        self,
        repository: SqliteEventRepository,
        routine_retention_days: int = 7,
        critical_retention_days: int = 30,
        audit_logger: AuditLogger | None = None,
        check_interval_seconds: float = 3600.0,
    ) -> None:
        self.repository = repository
        self.routine_retention_days = routine_retention_days
        self.critical_retention_days = critical_retention_days
        self.audit_logger = audit_logger or AuditLogger(connection_manager=repository.manager)
        self.check_interval_seconds = check_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def purge_expired(self, now: datetime | None = None) -> RetentionResult:
        """Purge events and media files older than tiered retention limits."""
        ref_time = now or datetime.now(UTC)
        routine_cutoff = ref_time - timedelta(days=self.routine_retention_days)
        critical_cutoff = ref_time - timedelta(days=self.critical_retention_days)

        manager = self.repository.manager

        def _find_expired(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
            cursor = conn.cursor()
            # Find expired events: routine (< HIGH) older than 7d
            # or critical (>= HIGH) older than 30d
            cursor.execute(
                """
                SELECT id, media_uri, snapshot_uri FROM camera_events
                WHERE (threat_level < ? AND timestamp < ?)
                   OR (threat_level >= ? AND timestamp < ?);
                """,
                (
                    int(ThreatLevel.HIGH),
                    routine_cutoff.isoformat(),
                    int(ThreatLevel.HIGH),
                    critical_cutoff.isoformat(),
                ),
            )
            event_rows = cursor.fetchall()
            expired_event_ids = [row["id"] for row in event_rows]

            # Collect media file paths from camera_events
            files_to_check: set[str] = set()
            for row in event_rows:
                if row["media_uri"]:
                    files_to_check.add(row["media_uri"])
                if row["snapshot_uri"]:
                    files_to_check.add(row["snapshot_uri"])

            # Also check media_assets table
            if expired_event_ids:
                placeholders = ",".join("?" for _ in expired_event_ids)
                cursor.execute(
                    f"SELECT file_path FROM media_assets WHERE event_id IN ({placeholders});",
                    expired_event_ids,
                )
                for row in cursor.fetchall():
                    if row["file_path"]:
                        files_to_check.add(row["file_path"])

            return expired_event_ids, list(files_to_check)

        expired_ids, file_candidates = await manager.execute_read(_find_expired)

        # Prune physical files from disk
        deleted_files_count = 0
        bytes_freed = 0

        for file_path_str in file_candidates:
            try:
                # Support file:// URI or plain path
                raw_path = file_path_str
                if raw_path.startswith("file://"):
                    raw_path = raw_path[7:]
                p = Path(raw_path)
                if p.is_file():
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    deleted_files_count += 1
                    bytes_freed += size
            except OSError:
                pass

        # Delete database records
        events_purged = 0
        if expired_ids:

            def _delete_records(conn: sqlite3.Connection) -> int:
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in expired_ids)
                cursor.execute(
                    f"DELETE FROM camera_events WHERE id IN ({placeholders});",
                    expired_ids,
                )
                return cursor.rowcount

            events_purged = await manager.execute_write(_delete_records)

        result = RetentionResult(
            events_purged=events_purged,
            files_deleted=deleted_files_count,
            bytes_freed=bytes_freed,
        )

        # Record audit log entry
        await self.audit_logger.log(
            action="retention_purge",
            entity_type="system",
            details={
                "events_purged": result.events_purged,
                "files_deleted": result.files_deleted,
                "bytes_freed": result.bytes_freed,
                "routine_cutoff": routine_cutoff.isoformat(),
                "critical_cutoff": critical_cutoff.isoformat(),
            },
        )

        return result

    async def _run_loop(self) -> None:
        """Background loop executing scheduled retention purges."""
        while self._running:
            with contextlib.suppress(Exception):
                await self.purge_expired()
            await asyncio.sleep(self.check_interval_seconds)

    def start(self) -> None:
        """Start background retention worker."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop background retention worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
