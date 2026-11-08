"""Household members management for residents and pets."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lookout.adapters.sqlite.connection import SqliteConnectionManager


@dataclass
class HouseholdMember:
    """Enrolled household resident, pet, vehicle, or thing entity."""

    id: str
    name: str
    member_type: str = "resident"  # "resident", "pet", "vehicle", "thing"
    species: str | None = None
    enrolled_faces: list[str] = field(default_factory=list)
    notification_targets: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _row_to_member(row: sqlite3.Row) -> HouseholdMember:
    """Convert database row to HouseholdMember entity."""
    created = datetime.fromisoformat(row["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    updated = datetime.fromisoformat(row["updated_at"])
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)

    attributes: dict[str, Any] = {}
    if "attributes_json" in row and row["attributes_json"]:
        try:
            attributes = json.loads(row["attributes_json"])
        except Exception:
            attributes = {}

    return HouseholdMember(
        id=row["id"],
        name=row["name"],
        member_type=row["member_type"],
        species=row["species"],
        enrolled_faces=json.loads(row["enrolled_faces_json"]),
        notification_targets=json.loads(row["notification_targets_json"]),
        attributes=attributes,
        created_at=created,
        updated_at=updated,
    )


class HouseholdMembersRepository:
    """Repository for household members (residents, pets, vehicles, things) persistence."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        connection_manager: SqliteConnectionManager | None = None,
    ) -> None:
        self._manager = connection_manager or SqliteConnectionManager(db_path=db_path)

    async def save(self, member: HouseholdMember) -> None:
        """Persist or update household member."""

        def _write(conn: sqlite3.Connection) -> None:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO household_members (
                    id, member_type, name, species, enrolled_faces_json,
                    notification_targets_json, attributes_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    member_type = excluded.member_type,
                    name = excluded.name,
                    species = excluded.species,
                    enrolled_faces_json = excluded.enrolled_faces_json,
                    notification_targets_json = excluded.notification_targets_json,
                    attributes_json = excluded.attributes_json,
                    updated_at = excluded.updated_at;
                """,
                (
                    member.id,
                    member.member_type,
                    member.name,
                    member.species,
                    json.dumps(member.enrolled_faces),
                    json.dumps(member.notification_targets),
                    json.dumps(member.attributes),
                    member.created_at.isoformat(),
                    member.updated_at.isoformat(),
                ),
            )

        await self._manager.execute_write(_write)

    async def add_member(self, member: HouseholdMember) -> None:
        """Alias for save."""
        await self.save(member)

    async def get_by_id(self, member_id: str) -> HouseholdMember | None:
        """Retrieve household member by ID."""

        def _read(conn: sqlite3.Connection) -> HouseholdMember | None:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM household_members WHERE id = ?;", (member_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            return _row_to_member(row)

        return await self._manager.execute_read(_read)

    async def get_member(self, member_id: str) -> HouseholdMember | None:
        """Alias for get_by_id."""
        return await self.get_by_id(member_id)

    async def list_members(self, member_type: str | None = None) -> list[HouseholdMember]:
        """List household members, optionally filtered by member_type ('resident' or 'pet')."""

        def _read(conn: sqlite3.Connection) -> list[HouseholdMember]:
            cursor = conn.cursor()
            if member_type is not None:
                cursor.execute(
                    "SELECT * FROM household_members WHERE member_type = ? ORDER BY name ASC;",
                    (member_type,),
                )
            else:
                cursor.execute("SELECT * FROM household_members ORDER BY name ASC;")
            return [_row_to_member(row) for row in cursor.fetchall()]

        return await self._manager.execute_read(_read)

    async def delete(self, member_id: str) -> bool:
        """Delete household member by ID."""

        def _write(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM household_members WHERE id = ?;", (member_id,))
            return cursor.rowcount > 0

        return await self._manager.execute_write(_write)

    async def delete_member(self, member_id: str) -> bool:
        """Alias for delete."""
        return await self.delete(member_id)
