"""SQLite connection factory (WAL, busy timeout, FK enforcement) and versioned migrations."""

import sqlite3
from pathlib import Path

_SCHEMA_SQL_PATH = Path(__file__).with_name("schema.sql")

# Static ordered migrations; 001 applies the full schema.sql DDL (proposal.md §1).
_MIGRATIONS: tuple[tuple[int, str], ...] = ((1, _SCHEMA_SQL_PATH.read_text(encoding="utf-8")),)


class DatabaseError(Exception):
    """Raised when the SQLite store cannot be configured or migrated as required."""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL journaling, a 5s busy timeout, and FK enforcement."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    reported = str(row[0]).lower() if row is not None else "<no row returned>"
    if reported != "wal":
        conn.close()
        raise DatabaseError(f"journal_mode=WAL was not honored (store reported {reported!r})")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection, applied_at: str) -> None:
    """Apply pending migrations in order, recording each version with the given timestamp."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations"
        " (version INTEGER PRIMARY KEY, applied_at TEXT)"
    )
    conn.commit()
    applied = {int(row["version"]) for row in conn.execute("SELECT version FROM schema_migrations")}
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
