"""SQLite connection factory (WAL, busy timeout, FK enforcement) and versioned migrations."""

import sqlite3
from pathlib import Path

_SCHEMA_SQL_PATH = Path(__file__).with_name("schema.sql")

# Static ordered migrations; 001 applies the full schema.sql DDL (proposal.md §1).
_MIGRATIONS: tuple[tuple[int, str], ...] = ((1, _SCHEMA_SQL_PATH.read_text(encoding="utf-8")),)


# Subclasses sqlite3.Error (mirroring sqlite3's own DatabaseError placement) so adapter-layer
# callers that catch sqlite3.Error keep catching typed failures; all raises here are DatabaseError.
class DatabaseError(sqlite3.Error):
    """Raised when the SQLite store cannot be configured or migrated as required."""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL journaling, a 5s busy timeout, and FK enforcement.

    check_same_thread is lifted (upstream Hermes calls tool handlers from worker threads
    while the plugin's connection is opened during discovery): concurrent use stays safe
    because SQLite runs in serialized mode and busy_timeout serializes lock contention;
    handlers keep to short single-statement transactions.
    """
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
    except sqlite3.Error as exc:
        raise DatabaseError(f"cannot open SQLite store {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    except sqlite3.Error as exc:
        conn.close()
        raise DatabaseError(f"cannot open SQLite store {path}: {exc}") from exc
    reported = str(row[0]).lower() if row is not None else "<no row returned>"
    if reported != "wal":
        conn.close()
        raise DatabaseError(f"journal_mode=WAL was not honored (store reported {reported!r})")
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error as exc:
        conn.close()
        raise DatabaseError(f"cannot configure SQLite store {path}: {exc}") from exc
    return conn


def migrate(conn: sqlite3.Connection, applied_at: str) -> None:
    """Apply pending migrations in order, recording each version with the given timestamp."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations"
            " (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.commit()
        rows = conn.execute("SELECT version FROM schema_migrations")
        applied = {int(row["version"]) for row in rows}
        for version, script in _MIGRATIONS:
            if version in applied:
                continue
            try:
                conn.executescript(f"BEGIN;\n{script}")  # transaction stays open after the script
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, applied_at),
                )
            except BaseException:
                conn.rollback()  # partial DDL is undone; the version stays unrecorded
                raise
            conn.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"migration failed: {exc}") from exc
