"""SQLite persistence, FTS5 search engine, and retention adapters."""

from lookout.adapters.sqlite.audit import AuditLogger
from lookout.adapters.sqlite.connection import SqliteConnectionManager
from lookout.adapters.sqlite.members import HouseholdMember, HouseholdMembersRepository
from lookout.adapters.sqlite.migration import ImportResult, import_legacy_events
from lookout.adapters.sqlite.repository import SqliteEventRepository
from lookout.adapters.sqlite.retention import MediaRetentionWorker, RetentionResult
from lookout.adapters.sqlite.schema import initialize_database

__all__ = [
    "AuditLogger",
    "HouseholdMember",
    "HouseholdMembersRepository",
    "ImportResult",
    "MediaRetentionWorker",
    "RetentionResult",
    "SqliteConnectionManager",
    "SqliteEventRepository",
    "import_legacy_events",
    "initialize_database",
]
