"""Unit tests for household members repository and audit logging."""

from pathlib import Path

import pytest

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.sqlite.members import HouseholdMember, HouseholdMembersRepository


@pytest.mark.asyncio
async def test_household_members_crud(tmp_path: Path) -> None:
    """TR-25: Verify CRUD lifecycle for household residents and pets."""
    db_file = tmp_path / "members_test.db"
    repo = HouseholdMembersRepository(db_path=db_file)

    # 1. Create resident
    resident = HouseholdMember(
        id="res-1",
        name="Alice",
        member_type="resident",
        enrolled_faces=["/faces/alice_1.jpg", "/faces/alice_2.jpg"],
        notification_targets=["primary-webhook"],
    )
    await repo.save(resident)

    # 2. Retrieve resident
    member = await repo.get_by_id("res-1")
    assert member is not None
    assert member.name == "Alice"
    assert member.member_type == "resident"
    assert len(member.enrolled_faces) == 2
    assert "primary-webhook" in member.notification_targets

    # 3. Create pet
    pet = HouseholdMember(
        id="pet-1",
        name="Buster",
        member_type="pet",
        species="dog",
    )
    await repo.save(pet)

    # 4. List members by type
    residents = await repo.list_members(member_type="resident")
    assert len(residents) == 1
    assert residents[0].id == "res-1"

    pets = await repo.list_members(member_type="pet")
    assert len(pets) == 1
    assert pets[0].name == "Buster"

    # 5. Update resident
    resident.name = "Alice Smith"
    await repo.save(resident)
    updated = await repo.get_by_id("res-1")
    assert updated is not None
    assert updated.name == "Alice Smith"

    # 6. Delete member
    assert await repo.delete("res-1") is True
    assert await repo.get_by_id("res-1") is None
    assert await repo.delete("res-1") is False


@pytest.mark.asyncio
async def test_audit_log_recording_and_query(tmp_path: Path) -> None:
    """TR-26: Verify structured audit logging and retrieval."""
    db_file = tmp_path / "audit_test.db"
    logger = AuditLogger(db_path=db_file)

    entry_id = await logger.log(
        action="member_enrolled",
        entity_type="member",
        entity_id="res-1",
        details={"name": "Alice", "enrolled_faces": 2},
    )
    assert entry_id is not None

    logs = await logger.query(action="member_enrolled")
    assert len(logs) == 1
    assert logs[0]["action"] == "member_enrolled"
    assert logs[0]["entity_type"] == "member"
    assert logs[0]["entity_id"] == "res-1"
    assert logs[0]["details"]["name"] == "Alice"
