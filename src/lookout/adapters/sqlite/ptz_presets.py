"""PTZ preset persistence repository."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lookout.adapters.sqlite.connection import SqliteConnectionManager


@dataclass
class PtzPreset:
    """A saved PTZ camera preset position."""

    camera_id: str
    name: str
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _row_to_preset(row: sqlite3.Row) -> PtzPreset:
    created = datetime.fromisoformat(row["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return PtzPreset(
        camera_id=row["camera_id"],
        name=row["name"],
        pan=float(row["pan"]),
        tilt=float(row["tilt"]),
        zoom=float(row["zoom"]),
        created_at=created,
    )


class PtzPresetRepository:
    """Repository for PTZ preset persistence."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        connection_manager: SqliteConnectionManager | None = None,
    ) -> None:
        self._manager = connection_manager or SqliteConnectionManager(db_path=db_path)

    async def save(self, preset: PtzPreset) -> None:
        def _write(conn: sqlite3.Connection) -> None:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ptz_presets (camera_id, name, pan, tilt, zoom, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_id, name) DO UPDATE SET
                    pan=excluded.pan,
                    tilt=excluded.tilt,
                    zoom=excluded.zoom,
                    created_at=excluded.created_at;
                """,
                (
                    preset.camera_id,
                    preset.name,
                    preset.pan,
                    preset.tilt,
                    preset.zoom,
                    preset.created_at.isoformat(),
                ),
            )

        await self._manager.execute_write(_write)

    async def add(self, preset: PtzPreset) -> None:
        await self.save(preset)

    async def create(self, preset: PtzPreset) -> None:
        await self.save(preset)

    async def upsert(self, preset: PtzPreset) -> None:
        await self.save(preset)

    async def get(self, camera_id: str, name: str | None = None) -> PtzPreset | None:
        # Support get(camera_id, name) and get(composite) maybe
        if name is None:
            # maybe camera_id contains "cam:name"
            if ":" in str(camera_id):
                parts = str(camera_id).split(":", 1)
                camera_id, name = parts[0], parts[1]
            else:
                return None

        def _read(conn: sqlite3.Connection) -> PtzPreset | None:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM ptz_presets WHERE camera_id = ? AND name = ?;",
                (camera_id, name),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return _row_to_preset(row)

        return await self._manager.execute_read(_read)

    async def get_preset(self, camera_id: str, name: str) -> PtzPreset | None:
        return await self.get(camera_id, name)

    async def get_by_name(self, camera_id: str, name: str) -> PtzPreset | None:
        return await self.get(camera_id, name)

    async def get_by_id(self, preset_id: str) -> PtzPreset | None:
        if ":" in preset_id:
            cam, name = preset_id.split(":", 1)
            return await self.get(cam, name)
        return None

    async def list_presets(self, camera_id: str | None = None) -> list[PtzPreset]:
        def _read(conn: sqlite3.Connection) -> list[PtzPreset]:
            cursor = conn.cursor()
            if camera_id is not None:
                cursor.execute(
                    "SELECT * FROM ptz_presets WHERE camera_id = ? ORDER BY name ASC;",
                    (camera_id,),
                )
            else:
                cursor.execute("SELECT * FROM ptz_presets ORDER BY camera_id, name ASC;")
            return [_row_to_preset(row) for row in cursor.fetchall()]

        return await self._manager.execute_read(_read)

    async def list_for_camera(self, camera_id: str) -> list[PtzPreset]:
        return await self.list_presets(camera_id)

    async def delete(self, camera_id: str, name: str | None = None) -> bool:
        if name is None:
            if ":" in str(camera_id):
                parts = str(camera_id).split(":", 1)
                camera_id, name = parts[0], parts[1]
            else:
                return False

        def _write(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM ptz_presets WHERE camera_id = ? AND name = ?;",
                (camera_id, name),
            )
            return cursor.rowcount > 0

        return await self._manager.execute_write(_write)

    async def delete_preset(self, camera_id: str, name: str) -> bool:
        return await self.delete(camera_id, name)


PtzPresetRepository.list = PtzPresetRepository.list_presets  # type: ignore[attr-defined]
