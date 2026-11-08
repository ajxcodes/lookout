"""SQLite database schema, DDL statements, FTS5 virtual tables, and initialization."""

import contextlib
import sqlite3

SCHEMA_DDL = """
-- Aggregate camera events table
CREATE TABLE IF NOT EXISTS camera_events (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    threat_level INTEGER NOT NULL,
    summary TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    resident_id TEXT,
    pet_id TEXT,
    media_uri TEXT,
    snapshot_uri TEXT,
    metadata_json TEXT NOT NULL,
    zone_id TEXT,
    vehicle_id TEXT,
    matched_entities_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_camera_events_timestamp ON camera_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_camera_events_camera_id ON camera_events(camera_id);
CREATE INDEX IF NOT EXISTS idx_camera_events_threat_level ON camera_events(threat_level);
CREATE INDEX IF NOT EXISTS idx_camera_events_category ON camera_events(category);
CREATE INDEX IF NOT EXISTS idx_camera_events_zone_id ON camera_events(zone_id);
CREATE INDEX IF NOT EXISTS idx_camera_events_vehicle_id ON camera_events(vehicle_id);

-- Media assets tracking associated media files
CREATE TABLE IF NOT EXISTS media_assets (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES camera_events(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_assets_event_id ON media_assets(event_id);
CREATE INDEX IF NOT EXISTS idx_media_assets_expires_at ON media_assets(expires_at);

-- Household members table for residents, pets, vehicles, and things
CREATE TABLE IF NOT EXISTS household_members (
    id TEXT PRIMARY KEY,
    member_type TEXT NOT NULL,
    name TEXT NOT NULL,
    species TEXT,
    enrolled_faces_json TEXT NOT NULL DEFAULT '[]',
    notification_targets_json TEXT NOT NULL DEFAULT '[]',
    attributes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_household_members_type ON household_members(member_type);

-- Structured audit logging table
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);

-- FTS5 full-text search table
CREATE VIRTUAL TABLE IF NOT EXISTS camera_events_fts USING fts5(
    event_id UNINDEXED,
    summary,
    category,
    tags,
    zone_id,
    matched_entities,
    tokenize = 'porter'
);

-- Triggers for automatic FTS synchronization
CREATE TRIGGER IF NOT EXISTS trg_camera_events_ai AFTER INSERT ON camera_events
BEGIN
    INSERT INTO camera_events_fts(event_id, summary, category, tags, zone_id, matched_entities)
    VALUES (
        NEW.id,
        NEW.summary,
        NEW.category,
        COALESCE(json_extract(NEW.metadata_json, '$.tags'), ''),
        COALESCE(NEW.zone_id, ''),
        COALESCE(NEW.matched_entities_json, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_camera_events_ad AFTER DELETE ON camera_events
BEGIN
    DELETE FROM camera_events_fts WHERE event_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_camera_events_au AFTER UPDATE ON camera_events
BEGIN
    DELETE FROM camera_events_fts WHERE event_id = OLD.id;
    INSERT INTO camera_events_fts(event_id, summary, category, tags, zone_id, matched_entities)
    VALUES (
        NEW.id,
        NEW.summary,
        NEW.category,
        COALESCE(json_extract(NEW.metadata_json, '$.tags'), ''),
        COALESCE(NEW.zone_id, ''),
        COALESCE(NEW.matched_entities_json, '')
    );
END;
"""


def initialize_database(
    connection: sqlite3.Connection,
    wal_mode: bool = True,
    busy_timeout_ms: int = 5000,
) -> None:
    """Apply PRAGMA configurations and create database tables and triggers idempotently."""
    cursor = connection.cursor()
    if wal_mode:
        cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms};")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.executescript(SCHEMA_DDL)

    # Migrate any existing tables with missing columns
    for col_ddl in [
        "ALTER TABLE camera_events ADD COLUMN zone_id TEXT;",
        "ALTER TABLE camera_events ADD COLUMN vehicle_id TEXT;",
        "ALTER TABLE camera_events ADD COLUMN matched_entities_json TEXT NOT NULL DEFAULT '[]';",
        "ALTER TABLE household_members ADD COLUMN attributes_json TEXT NOT NULL DEFAULT '{}';",
    ]:
        with contextlib.suppress(sqlite3.OperationalError):
            cursor.execute(col_ddl)

    connection.commit()
