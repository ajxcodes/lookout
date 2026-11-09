"""SQLite event repository implementing EventRepositoryPort and ViewerApiPort."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lookout.adapters.sqlite.connection import SqliteConnectionManager
from lookout.domain.models import (
    BoundingBox,
    CameraEvent,
    DetectionMetrics,
    EventCategory,
    InferenceProvenance,
    ThreatLevel,
)
from lookout.ports.egress import EventRepositoryPort
from lookout.ports.ingress import ViewerApiPort


def _sanitize_fts_query(raw_query: str) -> str:
    """Sanitize raw user query into safe FTS5 query tokens with prefix and boolean support."""
    cleaned = re.sub(r"\b(has|resident|vehicle|zone):", "", raw_query)
    raw_tokens = re.findall(r"[\w*]+", cleaned)
    if not raw_tokens:
        return ""

    tokens: list[str] = []
    for t in raw_tokens:
        if t in ("OR", "AND", "NOT"):
            if tokens and tokens[-1] not in ("OR", "AND", "NOT"):
                tokens.append(t)
        else:
            clean = t.strip("*")
            if clean:
                tokens.append(f'"{clean}"*')

    while tokens and tokens[-1] in ("OR", "AND", "NOT"):
        tokens.pop()

    return " ".join(tokens)


def _serialize_metrics(metrics: DetectionMetrics) -> str:
    """Serialize DetectionMetrics to JSON string."""
    return json.dumps(
        {
            "confidence": metrics.confidence,
            "bounding_boxes": [
                {
                    "label": b.label,
                    "confidence": b.confidence,
                    "x_min": b.x_min,
                    "y_min": b.y_min,
                    "x_max": b.x_max,
                    "y_max": b.y_max,
                    "entity_id": b.entity_id,
                    "entity_name": b.entity_name,
                }
                for b in metrics.bounding_boxes
            ],
            "audio_decibels": metrics.audio_decibels,
            "duration_seconds": metrics.duration_seconds,
            "total_latency_ms": metrics.total_latency_ms,
            "tracked_entities": metrics.tracked_entities,
            "posture_telemetry": metrics.posture_telemetry,
            "zone_id": metrics.zone_id,
        }
    )


def _deserialize_metrics(raw_json: str) -> DetectionMetrics:
    """Deserialize DetectionMetrics from JSON string."""
    data = json.loads(raw_json)
    bounding_boxes = [
        BoundingBox(
            label=bb["label"],
            confidence=float(bb["confidence"]),
            x_min=float(bb["x_min"]),
            y_min=float(bb["y_min"]),
            x_max=float(bb["x_max"]),
            y_max=float(bb["y_max"]),
            entity_id=bb.get("entity_id"),
            entity_name=bb.get("entity_name"),
        )
        for bb in data.get("bounding_boxes", [])
    ]
    return DetectionMetrics(
        confidence=float(data.get("confidence", 0.0)),
        bounding_boxes=bounding_boxes,
        audio_decibels=data.get("audio_decibels"),
        duration_seconds=float(data.get("duration_seconds", 0.0)),
        total_latency_ms=float(data.get("total_latency_ms", 0.0)),
        tracked_entities=data.get("tracked_entities", []),
        posture_telemetry=data.get("posture_telemetry"),
        zone_id=data.get("zone_id"),
    )


def _serialize_provenance(provenance_list: list[InferenceProvenance]) -> str:
    """Serialize list of InferenceProvenance to JSON string."""
    return json.dumps(
        [
            {
                "provider": p.provider,
                "model_name": p.model_name,
                "latency_ms": p.latency_ms,
                "confidence": p.confidence,
                "tokens_used": p.tokens_used,
                "raw_output": p.raw_output,
                "timestamp": p.timestamp.isoformat(),
            }
            for p in provenance_list
        ]
    )


def _deserialize_provenance(raw_json: str) -> list[InferenceProvenance]:
    """Deserialize list of InferenceProvenance from JSON string."""
    data = json.loads(raw_json)
    result: list[InferenceProvenance] = []
    for p in data:
        ts = datetime.fromisoformat(p["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        result.append(
            InferenceProvenance(
                provider=p["provider"],
                model_name=p["model_name"],
                latency_ms=float(p["latency_ms"]),
                confidence=float(p["confidence"]),
                tokens_used=p.get("tokens_used"),
                raw_output=p.get("raw_output"),
                timestamp=ts,
            )
        )
    return result


def _row_to_event(row: sqlite3.Row) -> CameraEvent:
    """Convert database row to CameraEvent domain model."""
    ts = datetime.fromisoformat(row["timestamp"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    keys = row.keys()
    zone_id = row["zone_id"] if "zone_id" in keys else None
    vehicle_id = row["vehicle_id"] if "vehicle_id" in keys else None
    matched_entities = (
        json.loads(row["matched_entities_json"])
        if "matched_entities_json" in keys and row["matched_entities_json"]
        else []
    )
    metadata = (
        json.loads(row["metadata_json"]) if "metadata_json" in keys and row["metadata_json"] else {}
    )
    is_bookmarked = bool(
        (row["bookmarked"] if "bookmarked" in keys and row["bookmarked"] is not None else False)
        or metadata.get("bookmarked", False)
    )
    if is_bookmarked:
        metadata["bookmarked"] = True

    return CameraEvent(
        id=row["id"],
        camera_id=row["camera_id"],
        timestamp=ts,
        category=EventCategory(row["category"]),
        threat_level=ThreatLevel(row["threat_level"]),
        summary=row["summary"],
        metrics=_deserialize_metrics(row["metrics_json"]),
        provenance=_deserialize_provenance(row["provenance_json"]),
        resident_id=row["resident_id"],
        pet_id=row["pet_id"],
        media_uri=row["media_uri"],
        snapshot_uri=row["snapshot_uri"],
        metadata=metadata,
        zone_id=zone_id,
        vehicle_id=vehicle_id,
        matched_entities=matched_entities,
        bookmarked=is_bookmarked,
    )


class SqliteEventRepository(EventRepositoryPort, ViewerApiPort):
    """SQLite-backed event repository supporting FTS5 search, WAL mode, and flexible queries."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        wal_mode: bool = True,
        busy_timeout_ms: int = 5000,
        connection_manager: SqliteConnectionManager | None = None,
    ) -> None:
        self._manager = connection_manager or SqliteConnectionManager(
            db_path=db_path,
            wal_mode=wal_mode,
            busy_timeout_ms=busy_timeout_ms,
        )

    @property
    def manager(self) -> SqliteConnectionManager:
        """Return the underlying connection manager."""
        return self._manager

    async def save(self, event: CameraEvent) -> None:
        """Persist a camera event and synchronize related media assets."""

        def _write(conn: sqlite3.Connection) -> None:
            cursor = conn.cursor()
            is_bm = bool(event.bookmarked or event.metadata.get("bookmarked", False))
            if is_bm:
                event.bookmarked = True
                event.metadata["bookmarked"] = True

            cursor.execute(
                """
                INSERT INTO camera_events (
                    id, camera_id, timestamp, category, threat_level, summary,
                    metrics_json, provenance_json, resident_id, pet_id,
                    media_uri, snapshot_uri, metadata_json, zone_id, vehicle_id,
                    matched_entities_json, created_at, bookmarked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    camera_id = excluded.camera_id,
                    timestamp = excluded.timestamp,
                    category = excluded.category,
                    threat_level = excluded.threat_level,
                    summary = excluded.summary,
                    metrics_json = excluded.metrics_json,
                    provenance_json = excluded.provenance_json,
                    resident_id = excluded.resident_id,
                    pet_id = excluded.pet_id,
                    media_uri = excluded.media_uri,
                    snapshot_uri = excluded.snapshot_uri,
                    metadata_json = excluded.metadata_json,
                    zone_id = excluded.zone_id,
                    vehicle_id = excluded.vehicle_id,
                    matched_entities_json = excluded.matched_entities_json,
                    bookmarked = excluded.bookmarked;
                """,
                (
                    event.id,
                    event.camera_id,
                    event.timestamp.isoformat(),
                    event.category.value,
                    int(event.threat_level),
                    event.summary,
                    _serialize_metrics(event.metrics),
                    _serialize_provenance(event.provenance),
                    event.resident_id,
                    event.pet_id,
                    event.media_uri,
                    event.snapshot_uri,
                    json.dumps(event.metadata),
                    event.zone_id,
                    event.vehicle_id,
                    json.dumps(event.matched_entities),
                    datetime.now(UTC).isoformat(),
                    1 if is_bm else 0,
                ),
            )

            # Record media asset if media_uri is set
            if event.media_uri:
                retention_days = 30 if event.threat_level >= ThreatLevel.HIGH else 7
                expires_at = (event.timestamp + timedelta(days=retention_days)).isoformat()
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO media_assets (
                        id, event_id, asset_type, file_path, file_size_bytes, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        f"{event.id}-media",
                        event.id,
                        "clip",
                        event.media_uri,
                        0,
                        event.timestamp.isoformat(),
                        expires_at,
                    ),
                )

            # Record snapshot asset if snapshot_uri is set
            if event.snapshot_uri:
                retention_days = 30 if event.threat_level >= ThreatLevel.HIGH else 7
                expires_at = (event.timestamp + timedelta(days=retention_days)).isoformat()
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO media_assets (
                        id, event_id, asset_type, file_path, file_size_bytes, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        f"{event.id}-snapshot",
                        event.id,
                        "snapshot",
                        event.snapshot_uri,
                        0,
                        event.timestamp.isoformat(),
                        expires_at,
                    ),
                )

        await self._manager.execute_write(_write)

    async def get_by_id(self, event_id: str) -> CameraEvent | None:
        """Retrieve an event by ID."""

        def _read(conn: sqlite3.Connection) -> CameraEvent | None:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM camera_events WHERE id = ?;", (event_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            return _row_to_event(row)

        return await self._manager.execute_read(_read)

    async def find(
        self,
        camera_id: str | None = None,
        category: EventCategory | None = None,
        min_threat: ThreatLevel | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        zone_id: str | None = None,
        entity: str | None = None,
    ) -> list[CameraEvent]:
        """Find events matching search criteria with pagination and optional FTS search."""
        sanitized_query: str | None = None
        if query is not None:
            sanitized_query = _sanitize_fts_query(query)
            if not sanitized_query:
                return []

        def _read(conn: sqlite3.Connection) -> list[CameraEvent]:
            cursor = conn.cursor()
            conditions: list[str] = []
            params: list[Any] = []

            if sanitized_query is not None:
                from_clause = (
                    "FROM camera_events c JOIN camera_events_fts fts ON c.id = fts.event_id"
                )
                conditions.append("camera_events_fts MATCH ?")
                params.append(sanitized_query)
            else:
                from_clause = "FROM camera_events c"

            if camera_id is not None:
                conditions.append("c.camera_id = ?")
                params.append(camera_id)
            if category is not None:
                conditions.append("c.category = ?")
                params.append(category.value)
            if min_threat is not None:
                conditions.append("c.threat_level >= ?")
                params.append(int(min_threat))
            if start_time is not None:
                conditions.append("c.timestamp >= ?")
                params.append(start_time.isoformat())
            if end_time is not None:
                conditions.append("c.timestamp <= ?")
                params.append(end_time.isoformat())
            if zone_id is not None:
                conditions.append("c.zone_id = ?")
                params.append(zone_id)
            if entity is not None:
                conditions.append(
                    "(c.resident_id = ? OR c.pet_id = ? OR c.vehicle_id = ?"
                    " OR c.matched_entities_json LIKE ?)"
                )
                params.extend([entity, entity, entity, f"%{entity}%"])

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = (
                f"SELECT c.* {from_clause} {where_clause} "
                f"ORDER BY c.timestamp DESC LIMIT ? OFFSET ?;"
            )
            params.extend([limit, offset])

            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [_row_to_event(row) for row in rows]

        return await self._manager.execute_read(_read)

    async def count(self) -> int:
        """Return total number of saved events."""

        def _read(conn: sqlite3.Connection) -> int:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM camera_events;")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

        return await self._manager.execute_read(_read)

    async def delete(self, event_id: str) -> bool:
        """Delete an event by ID; returns True if deleted, False if not found."""

        def _write(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM camera_events WHERE id = ?;", (event_id,))
            return cursor.rowcount > 0

        return await self._manager.execute_write(_write)

    # ViewerApiPort implementation
    async def get_event(self, event_id: str) -> CameraEvent | None:
        """Retrieve a single event by unique identifier."""
        return await self.get_by_id(event_id)

    async def list_events(
        self,
        camera_id: str | None = None,
        category: EventCategory | None = None,
        min_threat: ThreatLevel | None = None,
        limit: int = 50,
        offset: int = 0,
        zone_id: str | None = None,
        entity: str | None = None,
    ) -> list[CameraEvent]:
        """List events filtered by camera, category, threat level, zone, or entity."""
        return await self.find(
            camera_id=camera_id,
            category=category,
            min_threat=min_threat,
            limit=limit,
            offset=offset,
            zone_id=zone_id,
            entity=entity,
        )

    async def get_timeline(self, start_time: datetime, end_time: datetime) -> list[CameraEvent]:
        """Retrieve events occurring within a specified time interval."""
        return await self.find(start_time=start_time, end_time=end_time, limit=1000)

    async def search(self, query: str, limit: int = 50, offset: int = 0) -> list[CameraEvent]:
        """Full-text search query across event summaries, categories, and tags."""
        return await self.find(query=query, limit=limit, offset=offset)

    async def migrate_event_media(
        self,
        event_id: str,
        new_media_uri: str | None,
        new_snapshot_uri: str | None = None,
        new_tier: str = "archive",
    ) -> bool:
        """Update media URIs and record new storage tier in event metadata."""

        def _write(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT media_uri, snapshot_uri, metadata_json FROM camera_events WHERE id = ?;",
                (event_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            meta = json.loads(row["metadata_json"] or "{}")
            meta["storage_tier"] = new_tier

            med_uri = new_media_uri if new_media_uri is not None else row["media_uri"]
            snap_uri = new_snapshot_uri if new_snapshot_uri is not None else row["snapshot_uri"]

            cursor.execute(
                """
                UPDATE camera_events
                SET media_uri = ?, snapshot_uri = ?, metadata_json = ?
                WHERE id = ?;
                """,
                (med_uri, snap_uri, json.dumps(meta), event_id),
            )
            if med_uri:
                cursor.execute(
                    "UPDATE media_assets SET file_path = ? WHERE event_id = ?;",
                    (med_uri, event_id),
                )
            return True

        return await self._manager.execute_write(_write)

    async def nullify_media_references(
        self,
        event_id: str,
        tombstone_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Nullify media references, record tombstone metadata, and prune media assets."""

        def _write(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute("SELECT metadata_json FROM camera_events WHERE id = ?;", (event_id,))
            row = cursor.fetchone()
            if not row:
                return False
            meta = json.loads(row["metadata_json"] or "{}")
            meta["media_purged"] = True
            if tombstone_metadata:
                meta.update(tombstone_metadata)

            cursor.execute(
                """
                UPDATE camera_events
                SET media_uri = NULL, snapshot_uri = NULL, metadata_json = ?
                WHERE id = ?;
                """,
                (json.dumps(meta), event_id),
            )
            cursor.execute("DELETE FROM media_assets WHERE event_id = ?;", (event_id,))
            return True

        return await self._manager.execute_write(_write)

    async def bookmark_event(self, event_id: str, bookmarked: bool = True) -> bool:
        """Set or update bookmark status for an event."""

        def _write(conn: sqlite3.Connection) -> bool:
            cursor = conn.cursor()
            cursor.execute("SELECT metadata_json FROM camera_events WHERE id = ?;", (event_id,))
            row = cursor.fetchone()
            if not row:
                return False
            meta = json.loads(row["metadata_json"] or "{}")
            meta["bookmarked"] = bookmarked

            cursor.execute(
                """
                UPDATE camera_events
                SET metadata_json = ?, bookmarked = ?
                WHERE id = ?;
                """,
                (json.dumps(meta), 1 if bookmarked else 0, event_id),
            )
            return True

        return await self._manager.execute_write(_write)

    async def get_eviction_candidates(
        self,
        limit: int = 100,
        preserve_bookmarked: bool = True,
        retention_mode: str = "fifo_oldest_first",
        tier: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query eviction candidates based on policy, tier, and bookmark protection."""

        def _read(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            cursor = conn.cursor()
            conditions: list[str] = ["(media_uri IS NOT NULL OR snapshot_uri IS NOT NULL)"]
            params: list[Any] = []

            if tier == "archive":
                conditions.append("json_extract(metadata_json, '$.storage_tier') = 'archive'")
            elif tier == "local":
                conditions.append(
                    "(json_extract(metadata_json, '$.storage_tier') IS NULL "
                    "OR json_extract(metadata_json, '$.storage_tier') != 'archive')"
                )

            if preserve_bookmarked:
                conditions.append(
                    "(bookmarked = 0 OR bookmarked IS NULL) AND "
                    "(json_extract(metadata_json, '$.bookmarked') IS NULL "
                    "OR json_extract(metadata_json, '$.bookmarked') = 0 "
                    "OR json_extract(metadata_json, '$.bookmarked') = false)"
                )

            where_clause = " AND ".join(conditions)
            order_clause = (
                "threat_level ASC, timestamp ASC"
                if retention_mode == "events_priority"
                else "timestamp ASC"
            )

            query = (
                f"SELECT * FROM camera_events WHERE {where_clause} ORDER BY {order_clause} LIMIT ?;"
            )
            params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            candidates: list[dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                item["event"] = _row_to_event(r)
                item["metadata"] = json.loads(r["metadata_json"] or "{}")
                candidates.append(item)
            return candidates

        return await self._manager.execute_read(_read)

    def close(self) -> None:
        """Close underlying database connection manager."""
        self._manager.close()

    async def aclose(self) -> None:
        """Close underlying database connection manager asynchronously."""
        await self._manager.aclose()

    async def __aenter__(self) -> "SqliteEventRepository":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.aclose()
