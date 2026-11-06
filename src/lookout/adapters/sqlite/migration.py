"""Legacy historical event import migration utility and CLI."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.domain.models import (
    BoundingBox,
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)


@dataclass
class ImportResult:
    """Outcome of historical event import migration."""

    total_found: int = 0
    imported_count: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_timestamp(val: Any) -> datetime:
    """Parse timestamp from ISO-8601 string or numeric epoch."""
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=UTC)
    if isinstance(val, str):
        try:
            ts = datetime.fromisoformat(val)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return ts
        except ValueError:
            pass
    return datetime.now(UTC)


def _parse_category(val: Any) -> EventCategory:
    """Parse and normalize category string to EventCategory enum."""
    if isinstance(val, EventCategory):
        return val
    if isinstance(val, str):
        val_lower = val.lower().strip()
        # Direct enum value match
        for cat in EventCategory:
            if cat.value == val_lower:
                return cat
        # Fuzzy alias match
        alias_map = {
            "resident": EventCategory.VERIFIED_RESIDENT,
            "pet": EventCategory.KNOWN_PET,
            "dog": EventCategory.KNOWN_PET,
            "cat": EventCategory.KNOWN_PET,
            "courier": EventCategory.COURIER_DELIVERY,
            "delivery": EventCategory.COURIER_DELIVERY,
            "package": EventCategory.PACKAGE_EVENT,
            "visitor": EventCategory.UNKNOWN_VISITOR,
            "person": EventCategory.UNKNOWN_VISITOR,
            "vehicle": EventCategory.VEHICLE,
            "car": EventCategory.VEHICLE,
            "wildlife": EventCategory.ANIMAL_WILDLIFE,
            "animal": EventCategory.ANIMAL_WILDLIFE,
            "intrusion": EventCategory.SECURITY_INTRUSION,
            "intruder": EventCategory.SECURITY_INTRUSION,
            "acoustic": EventCategory.ACOUSTIC_ANOMALY,
            "audio": EventCategory.ACOUSTIC_ANOMALY,
            "false_positive": EventCategory.FALSE_POSITIVE,
        }
        if val_lower in alias_map:
            return alias_map[val_lower]
    return EventCategory.UNKNOWN_VISITOR


def _parse_threat_level(val: Any, is_threat_flag: bool | None = None) -> ThreatLevel:
    """Parse threat level from numeric, string, or boolean flag."""
    if isinstance(val, ThreatLevel):
        return val
    if isinstance(val, int):
        clamped = max(0, min(val, 4))
        return ThreatLevel(clamped)
    if isinstance(val, str):
        val_upper = val.upper().strip()
        if val_upper in ThreatLevel.__members__:
            return ThreatLevel[val_upper]
    if is_threat_flag is True:
        return ThreatLevel.MEDIUM
    return ThreatLevel.LOW


def _parse_metrics(raw: dict[str, Any]) -> DetectionMetrics:
    """Extract DetectionMetrics from raw legacy dict."""
    metrics_raw = raw.get("metrics")
    if isinstance(metrics_raw, dict):
        confidence = float(metrics_raw.get("confidence", 0.0))
        boxes: list[BoundingBox] = []
        for bb in metrics_raw.get("bounding_boxes", []):
            if isinstance(bb, dict):
                boxes.append(
                    BoundingBox(
                        label=str(bb.get("label", "unknown")),
                        confidence=float(bb.get("confidence", 0.0)),
                        x_min=float(bb.get("x_min", 0.0)),
                        y_min=float(bb.get("y_min", 0.0)),
                        x_max=float(bb.get("x_max", 0.0)),
                        y_max=float(bb.get("y_max", 0.0)),
                    )
                )
        return DetectionMetrics(
            confidence=confidence,
            bounding_boxes=boxes,
            audio_decibels=metrics_raw.get("audio_decibels"),
            duration_seconds=float(metrics_raw.get("duration_seconds", 0.0)),
            total_latency_ms=float(metrics_raw.get("total_latency_ms", 0.0)),
        )

    # Fallback to top-level confidence if present
    confidence = float(raw.get("confidence", 0.0))
    return DetectionMetrics(confidence=confidence)


def _parse_provenance(raw: dict[str, Any]) -> list[InferenceProvenance]:
    """Extract list of InferenceProvenance from raw legacy dict."""
    prov_raw = raw.get("provenance")
    if isinstance(prov_raw, list):
        result: list[InferenceProvenance] = []
        for p in prov_raw:
            if isinstance(p, dict):
                ts = _parse_timestamp(p.get("timestamp"))
                result.append(
                    InferenceProvenance(
                        provider=str(p.get("provider", "legacy")),
                        model_name=str(p.get("model_name", "unknown")),
                        latency_ms=float(p.get("latency_ms", 0.0)),
                        confidence=float(p.get("confidence", 0.0)),
                        tokens_used=p.get("tokens_used"),
                        raw_output=p.get("raw_output"),
                        timestamp=ts,
                    )
                )
        return result
    return []


KNOWN_MAPPED_FIELDS = {
    "id",
    "event_id",
    "camera_id",
    "camera",
    "timestamp",
    "time",
    "created_at",
    "category",
    "type",
    "label",
    "threat_level",
    "threat",
    "severity",
    "is_threat",
    "summary",
    "description",
    "title",
    "metrics",
    "confidence",
    "bounding_boxes",
    "provenance",
    "resident_id",
    "pet_id",
    "media_uri",
    "video_path",
    "clip_uri",
    "snapshot_uri",
    "snapshot_path",
    "image_uri",
    "metadata",
}


def _convert_raw_entry(raw: dict[str, Any], default_id: str | None = None) -> CameraEvent:
    """Transform a raw legacy dictionary record into a pure CameraEvent domain model."""
    event_id = str(raw.get("id") or raw.get("event_id") or default_id or uuid.uuid4())
    camera_id = str(raw.get("camera_id") or raw.get("camera") or "default-camera")

    ts_val = raw.get("timestamp") or raw.get("time") or raw.get("created_at")
    timestamp = _parse_timestamp(ts_val)

    cat_val = raw.get("category") or raw.get("type") or raw.get("label")
    category = _parse_category(cat_val)

    threat_val = raw.get("threat_level") or raw.get("threat") or raw.get("severity")
    is_threat = raw.get("is_threat")
    threat_level = _parse_threat_level(threat_val, is_threat)

    summary = str(
        raw.get("summary") or raw.get("description") or raw.get("title") or f"Event {event_id}"
    )

    metrics = _parse_metrics(raw)
    provenance = _parse_provenance(raw)

    resident_id = raw.get("resident_id")
    pet_id = raw.get("pet_id")
    media_uri = raw.get("media_uri") or raw.get("video_path") or raw.get("clip_uri")
    snapshot_uri = raw.get("snapshot_uri") or raw.get("snapshot_path") or raw.get("image_uri")

    # Preserve all unmapped fields in metadata["legacy"]
    metadata = dict(raw.get("metadata", {})) if isinstance(raw.get("metadata"), dict) else {}
    legacy_unmapped: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in KNOWN_MAPPED_FIELDS:
            legacy_unmapped[k] = v

    metadata["legacy"] = legacy_unmapped

    return CameraEvent(
        id=event_id,
        camera_id=camera_id,
        timestamp=timestamp,
        category=category,
        threat_level=threat_level,
        summary=summary,
        metrics=metrics,
        provenance=provenance,
        resident_id=resident_id,
        pet_id=pet_id,
        media_uri=media_uri,
        snapshot_uri=snapshot_uri,
        metadata=metadata,
    )


async def import_legacy_events(
    json_path: str | Path,
    repository: SqliteEventRepository,
    audit_logger: AuditLogger | None = None,
) -> ImportResult:
    """Import historical events from a legacy JSON file into SQLite with zero data loss."""
    path = Path(json_path)
    if not path.is_file():
        return ImportResult(errors=[f"File not found: {path}"])

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        return ImportResult(errors=[f"Failed to read JSON: {e}"])

    raw_entries: list[tuple[str | None, dict[str, Any]]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                raw_entries.append((None, item))
    elif isinstance(data, dict):
        if "events" in data and isinstance(data["events"], list):
            for item in data["events"]:
                if isinstance(item, dict):
                    raw_entries.append((None, item))
        elif "events" in data and isinstance(data["events"], dict):
            for k, item in data["events"].items():
                if isinstance(item, dict):
                    raw_entries.append((k, item))
        else:
            # Keyed dictionary format: {"evt-1": {...}, "evt-2": {...}}
            for k, item in data.items():
                if isinstance(item, dict):
                    raw_entries.append((k, item))

    result = ImportResult(total_found=len(raw_entries))
    logger = audit_logger or AuditLogger(connection_manager=repository.manager)

    for default_id, entry in raw_entries:
        try:
            event = _convert_raw_entry(entry, default_id)
            await repository.save(event)
            result.imported_count += 1
        except Exception as e:
            result.errors.append(f"Failed importing entry {default_id}: {e}")

    # Record historical migration audit entry
    await logger.log(
        action="historical_import",
        entity_type="event",
        details={
            "source_file": str(path.resolve()),
            "total_found": result.total_found,
            "imported_count": result.imported_count,
            "errors": result.errors,
        },
    )

    return result


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Import legacy events.json into SQLite")
    parser.add_argument("json_path", help="Path to legacy events.json file")
    parser.add_argument("--db", default="./data/lookout.db", help="Path to SQLite database")
    args = parser.parse_args()

    async def _main() -> None:
        repo = SqliteEventRepository(db_path=args.db)
        res = await import_legacy_events(args.json_path, repo)
        print(
            f"Import complete: {res.imported_count}/{res.total_found} imported, "
            f"{len(res.errors)} errors."
        )

    asyncio.run(_main())
