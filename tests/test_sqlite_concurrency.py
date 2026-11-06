"""Concurrency verification tests for SQLite WAL mode."""

import asyncio
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.domain.models import CameraEvent, EventCategory, ThreatLevel


@pytest.mark.asyncio
async def test_concurrent_reads_do_not_block_writes(tmp_path: Path) -> None:
    """TR-11: Verify concurrent reads in WAL mode do not block write transactions."""
    db_file = tmp_path / "concurrency_wal.db"
    repo = SqliteEventRepository(db_path=db_file, wal_mode=True)
    now = datetime.now(UTC)

    # Seed initial event
    await repo.save(
        CameraEvent(
            id="seed-1",
            camera_id="cam-1",
            timestamp=now,
            category=EventCategory.COURIER_DELIVERY,
            threat_level=ThreatLevel.LOW,
            summary="Initial seed event",
        )
    )

    # Simulate a slow read that holds a read transaction open for 0.4 seconds
    def slow_read_sync() -> None:
        def _read_fn(conn: sqlite3.Connection) -> None:
            conn.execute("SELECT * FROM camera_events;").fetchall()
            time.sleep(0.4)

        asyncio.run(repo.manager.execute_read(_read_fn))

    # Launch slow read in separate thread
    read_task = asyncio.to_thread(slow_read_sync)
    # Ensure read has started
    await asyncio.sleep(0.05)

    # Perform a write while the read is active and measure duration
    t_start = time.perf_counter()
    await repo.save(
        CameraEvent(
            id="concurrent-write-1",
            camera_id="cam-1",
            timestamp=now,
            category=EventCategory.SECURITY_INTRUSION,
            threat_level=ThreatLevel.HIGH,
            summary="Concurrent intrusion event",
        )
    )
    write_duration = time.perf_counter() - t_start

    await read_task

    assert write_duration < 0.2
    assert await repo.count() == 2


@pytest.mark.asyncio
async def test_concurrent_multi_reader_multi_writer_load(tmp_path: Path) -> None:
    """TR-12: Verify concurrent multi-reader multi-writer load executes cleanly."""
    db_file = tmp_path / "load_test.db"
    repo = SqliteEventRepository(db_path=db_file, wal_mode=True)
    now = datetime.now(UTC)

    # 5 writers each writing 5 events = 25 events total
    num_writers = 5
    events_per_writer = 5

    async def writer_task(writer_id: int) -> None:
        for i in range(events_per_writer):
            event = CameraEvent(
                id=f"writer-{writer_id}-evt-{i}",
                camera_id=f"cam-{writer_id}",
                timestamp=now,
                category=EventCategory.UNKNOWN_VISITOR,
                threat_level=ThreatLevel.LOW,
                summary=f"Event {i} from writer {writer_id}",
            )
            await repo.save(event)
            await asyncio.sleep(0.01)

    async def reader_task() -> None:
        for _ in range(15):
            await repo.find(limit=10)
            await repo.count()
            await asyncio.sleep(0.005)

    # Spawn writers and readers concurrently
    writers = [writer_task(w) for w in range(num_writers)]
    readers = [reader_task() for _ in range(5)]

    await asyncio.gather(*writers, *readers)

    total_count = await repo.count()
    assert total_count == 25


@pytest.mark.asyncio
async def test_sqlite_connection_manager_async_lifecycle(tmp_path: Path) -> None:
    """Verify SqliteConnectionManager and SqliteEventRepository support async context manager."""
    db_file = tmp_path / "lifecycle.db"

    # Test SqliteEventRepository as async context manager
    async with SqliteEventRepository(db_path=db_file) as repo:
        now = datetime.now(UTC)
        await repo.save(
            CameraEvent(
                id="lifecycle-evt-1",
                camera_id="cam-1",
                timestamp=now,
                category=EventCategory.COURIER_DELIVERY,
                threat_level=ThreatLevel.LOW,
                summary="Lifecycle test event",
            )
        )
        assert await repo.count() == 1

    # Verify connection was closed
    assert repo.manager._write_conn is None

    # Test explicit aclose() on SqliteConnectionManager directly
    from lookout.adapters.sqlite.connection import SqliteConnectionManager

    manager = SqliteConnectionManager(db_path=tmp_path / "mgr_lifecycle.db")
    assert manager._write_conn is not None
    await manager.aclose()
    assert manager._write_conn is None

    # Idempotent closing
    await manager.aclose()
    manager.close()
