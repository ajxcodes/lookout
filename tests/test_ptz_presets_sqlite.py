"""Unit tests for PTZ presets SQLite storage."""

import sqlite3

import pytest

from lookout.adapters.sqlite.ptz_presets import PtzPreset, PtzPresetRepository
from lookout.adapters.sqlite.schema import initialize_database


def test_ptz_presets_table_created() -> None:
    """TR-16: ptz_presets table created."""
    conn = sqlite3.connect(":memory:")
    initialize_database(conn)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    table_names = [row[0] for row in cursor.fetchall()]
    assert "ptz_presets" in table_names


@pytest.mark.asyncio
async def test_ptz_preset_repository_round_trip() -> None:
    """TR-17: Preset repository round-trips a preset."""
    repo = PtzPresetRepository(db_path=":memory:")
    preset = PtzPreset(camera_id="cam1", name="home", pan=0.1, tilt=0.2, zoom=0.3)
    await repo.save(preset)
    loaded = await repo.get("cam1", "home")
    assert loaded is not None
    assert loaded.name == preset.name
