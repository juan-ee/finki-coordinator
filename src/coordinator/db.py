"""SQLite connection factory (WAL, busy timeout, FK enforcement) and versioned migrations."""

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

_SCHEMA_SQL_PATH = Path(__file__).with_name("schema.sql")

# A migration is an SQL script (run via executescript) or a callable run against the
# connection. Callables exist for guarded schema surgery plain SQL cannot express:
# the status_days drop (D4) must tolerate fresh v6 stores whose migration 001 never
# created the column, which SQLite has no conditional DDL form for.
MigrationScript = str | Callable[[sqlite3.Connection], None]


def _migrate_002_drop_status_days(conn: sqlite3.Connection) -> None:
    """Drop the dead status_days column (v5 -> v6, D4); no-op when it never existed."""
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(members)")}
    if "status_days" in columns:
        conn.execute("ALTER TABLE members DROP COLUMN status_days")


# Worker threads share the one runtime connection (see connect() below): SQLite's
# serialized mode keeps individual statements from tearing, but it does not make a
# multi-statement write sequence atomic against other threads — a concurrent commit
# on the shared connection could land between the sequence's statements and persist
# its partial, uncommitted writes. Any multi-statement write sequence on the shared
# connection must therefore hold this lock across its whole statements+commit span
# (MembersRepo.delete's cascade is the first such sequence); single-statement
# execute+commit handlers need no lock.
WRITE_TRANSACTION_LOCK = threading.RLock()

# Static ordered migrations; 001 applies the full schema.sql DDL (proposal.md §1);
# 002 drops the v5 status_days column (guarded no-op on fresh v6 stores — they converge).
_MIGRATIONS: tuple[tuple[int, MigrationScript], ...] = (
    (1, _SCHEMA_SQL_PATH.read_text(encoding="utf-8")),
    (2, _migrate_002_drop_status_days),
)


# Subclasses sqlite3.Error (mirroring sqlite3's own DatabaseError placement) so adapter-layer
# callers that catch sqlite3.Error keep catching typed failures; all raises here are DatabaseError.
class DatabaseError(sqlite3.Error):
    """Raised when the SQLite store cannot be configured or migrated as required."""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL journaling, a 5s busy timeout, and FK enforcement.

    check_same_thread is lifted: the connection is opened during plugin discovery but
    upstream Hermes invokes tool handlers from worker threads. Intra-connection safety
    rests on SQLite serialized mode (sqlite3.threadsafety == 3 on CPython default builds:
    statements cannot interleave or tear) plus handlers keeping to short single-statement
    transactions — a multi-statement write sequence must hold WRITE_TRANSACTION_LOCK.
    busy_timeout=5000 guards cross-connection contention (init_db, second processes).
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
                if isinstance(script, str):
                    conn.executescript(
                        f"BEGIN;\n{script}"
                    )  # transaction stays open after the script
                else:
                    conn.execute("BEGIN")
                    script(conn)
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
