"""SQLite repositories for members, checkins, settings and the knowledge cache."""

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from .db import WRITE_TRANSACTION_LOCK
from .knowledge import Chunk

_MEMBER_COLUMNS = (
    "SELECT id, name, telegram_id, timezone, wake, role, active, "
    "created_at, updated_at FROM members"
)
_CHECKIN_COLUMNS = (
    "SELECT id, member_id, date, done, next, blockers, source, created_at FROM checkins"
)

# Runtime knob defaults: SettingsRepo.get falls back to these when a key is not stored.
DEFAULTS: dict[str, str] = {
    "digest_chat": "dm",
    "nudge_limit": "2",
}


@dataclass
class Member:
    """One members row."""

    id: int
    name: str
    telegram_id: int | None
    timezone: str
    wake: str | None
    role: str | None
    active: int
    created_at: str | None
    updated_at: str | None


@dataclass
class Checkin:
    """One checkins row."""

    id: int
    member_id: int
    date: str
    done: str | None
    next: str | None
    blockers: str | None
    source: str
    created_at: str | None


class MembersRepository(Protocol):
    """Handler-facing contract for the members store."""

    def add(
        self,
        *,
        name: str,
        telegram_id: int | None = None,
        timezone: str = "UTC",
        wake: str | None = None,
        role: str | None = None,
        active: int = 1,
        created_at: str,
    ) -> Member:
        """Insert a member and return the stored row."""
        ...

    def update(
        self,
        member_id: int,
        *,
        updated_at: str,
        name: str | None = None,
        telegram_id: int | None = None,
        timezone: str | None = None,
        wake: str | None = None,
        role: str | None = None,
        active: int | None = None,
    ) -> Member | None:
        """Set the supplied columns on a member, stamping updated_at (None if id unknown)."""
        ...

    def get(self, member_id: int) -> Member | None:
        """Return one member by id, including inactive rows (None if absent)."""
        ...

    def delete(self, member_id: int) -> bool:
        """Remove the member and their check-ins (one commit); True when a row went."""
        ...

    def list(self, *, active: int | None = None) -> list[Member]:
        """Return members ordered by name, optionally filtered to the given active flag."""
        ...


class CheckinsRepository(Protocol):
    """Handler-facing contract for the check-in store."""

    def submit(
        self,
        *,
        member_id: int,
        date: str,
        done: str | None = None,
        next: str | None = None,
        blockers: str | None = None,
        source: str = "auto",
        created_at: str,
    ) -> Checkin:
        """Upsert the member's check-in for date, latest-wins, and return the stored row."""
        ...

    def by_date(self, date: str) -> list[Checkin]:
        """Return the date's check-ins ordered by member id."""
        ...


class SettingsRepository(Protocol):
    """Handler-facing contract for the settings store."""

    def get(self, key: str) -> str:
        """Return the stored value for key, falling back to DEFAULTS when the key is absent."""
        ...

    def set(self, key: str, value: str) -> None:
        """Insert or overwrite the stored value for key."""
        ...


class KnowledgeSearchError(Exception):
    """Raised when an FTS5 MATCH query is malformed (translated at the tool layer)."""


@dataclass(frozen=True)
class KnowledgeHit:
    """One search result: the cached chunk's locator (rank is bm25, lower = better)."""

    chunk_id: int
    file_id: str
    path: str
    title: str
    heading: str | None
    rank: float


class KnowledgeRepository(Protocol):
    """Handler-facing contract for the knowledge cache (Drive is the record; D2)."""

    def replace_file(
        self,
        *,
        file_id: str,
        path: str,
        title: str,
        modified_time: str,
        fetched_at: str,
        chunks: list[Chunk],
    ) -> int:
        """Rewrite one file's cache rows and FTS entries; return the chunk count."""
        ...

    def watermark(self) -> str | None:
        """Return MAX(modified_time) over cached rows (None when the cache is empty)."""
        ...

    def search(self, query: str, limit: int) -> list[KnowledgeHit]:
        """Return the top FTS5 hits (bm25, title 10:1 over body) for the query.

        Raises KnowledgeSearchError when the query is not a valid FTS5 MATCH query.
        """
        ...


def _nullable_int(row: sqlite3.Row, key: str) -> int | None:
    """Read an optional INTEGER column from a row."""
    raw = row[key]
    return None if raw is None else int(raw)


def _nullable_str(row: sqlite3.Row, key: str) -> str | None:
    """Read an optional TEXT column from a row."""
    raw = row[key]
    return None if raw is None else str(raw)


def _member_from_row(row: sqlite3.Row) -> Member:
    """Materialize a members row as a Member."""
    return Member(
        id=int(row["id"]),
        name=str(row["name"]),
        telegram_id=_nullable_int(row, "telegram_id"),
        timezone=str(row["timezone"]),
        wake=_nullable_str(row, "wake"),
        role=_nullable_str(row, "role"),
        active=int(row["active"]),
        created_at=_nullable_str(row, "created_at"),
        updated_at=_nullable_str(row, "updated_at"),
    )


def _checkin_from_row(row: sqlite3.Row) -> Checkin:
    """Materialize a checkins row as a Checkin."""
    return Checkin(
        id=int(row["id"]),
        member_id=int(row["member_id"]),
        date=str(row["date"]),
        done=_nullable_str(row, "done"),
        next=_nullable_str(row, "next"),
        blockers=_nullable_str(row, "blockers"),
        source=str(row["source"]),
        created_at=_nullable_str(row, "created_at"),
    )


class MembersRepo:
    """SQLite-backed members store."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the repository to an open, migrated connection."""
        self._conn = conn

    def add(
        self,
        *,
        name: str,
        telegram_id: int | None = None,
        timezone: str = "UTC",
        wake: str | None = None,
        role: str | None = None,
        active: int = 1,
        created_at: str,
    ) -> Member:
        """Insert a member and return the stored row."""
        cursor = self._conn.execute(
            "INSERT INTO members"
            " (name, telegram_id, timezone, wake, role, active,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                telegram_id,
                timezone,
                wake,
                role,
                active,
                created_at,
                created_at,  # a fresh row is born with updated_at = created_at
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None  # the INSERT above guarantees a rowid
        member = self.get(cursor.lastrowid)
        assert member is not None  # the INSERT above guarantees the row exists
        return member

    def update(
        self,
        member_id: int,
        *,
        updated_at: str,
        name: str | None = None,
        telegram_id: int | None = None,
        timezone: str | None = None,
        wake: str | None = None,
        role: str | None = None,
        active: int | None = None,
    ) -> Member | None:
        """Set the supplied columns on a member, stamping updated_at (None if id unknown)."""
        columns: dict[str, object] = {"updated_at": updated_at}
        if name is not None:
            columns["name"] = name
        if telegram_id is not None:
            columns["telegram_id"] = telegram_id
        if timezone is not None:
            columns["timezone"] = timezone
        if wake is not None:
            columns["wake"] = wake
        if role is not None:
            columns["role"] = role
        if active is not None:
            columns["active"] = active
        assignments = ", ".join(f"{column} = ?" for column in columns)
        cursor = self._conn.execute(
            f"UPDATE members SET {assignments} WHERE id = ?",
            [*columns.values(), member_id],
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            return None
        member = self.get(member_id)
        assert member is not None  # rowcount > 0 guarantees the row exists
        return member

    def get(self, member_id: int) -> Member | None:
        """Return one member by id, including inactive rows (None if absent)."""
        row = self._conn.execute(_MEMBER_COLUMNS + " WHERE id = ?", (member_id,)).fetchone()
        return None if row is None else _member_from_row(row)

    def delete(self, member_id: int) -> bool:
        """Remove the member's check-ins then the member (one commit); True when removed.

        FK enforcement (foreign_keys=ON) fixes the order: checkins first. Both DELETEs
        run as one write transaction held under WRITE_TRANSACTION_LOCK (worker threads
        share this connection, so a multi-statement sequence must not interleave with
        another thread's commit); any sqlite3.Error rolls the transaction back and
        re-raises, so the cascade is atomic — a failure leaves both tables untouched.
        """
        with WRITE_TRANSACTION_LOCK:
            try:
                self._conn.execute("DELETE FROM checkins WHERE member_id = ?", (member_id,))
                cursor = self._conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
                self._conn.commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
        return cursor.rowcount > 0

    def list(self, *, active: int | None = None) -> list[Member]:
        """Return members ordered by name, optionally filtered to the given active flag."""
        sql = _MEMBER_COLUMNS
        params: tuple[object, ...] = ()
        if active is not None:
            sql += " WHERE active = ?"
            params = (active,)
        rows = self._conn.execute(sql + " ORDER BY name", params).fetchall()
        return [_member_from_row(row) for row in rows]


class CheckinsRepo:
    """SQLite-backed check-in store (one row per member per date, latest wins)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the repository to an open, migrated connection."""
        self._conn = conn

    def submit(
        self,
        *,
        member_id: int,
        date: str,
        done: str | None = None,
        next: str | None = None,
        blockers: str | None = None,
        source: str = "auto",
        created_at: str,
    ) -> Checkin:
        """Upsert the member's check-in for date, latest-wins, and return the stored row."""
        self._conn.execute(
            "INSERT INTO checkins (member_id, date, done, next, blockers, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(member_id, date) DO UPDATE SET done = excluded.done,"
            " next = excluded.next, blockers = excluded.blockers, source = excluded.source,"
            " created_at = excluded.created_at",
            (member_id, date, done, next, blockers, source, created_at),
        )
        self._conn.commit()
        row = self._conn.execute(
            _CHECKIN_COLUMNS + " WHERE member_id = ? AND date = ?", (member_id, date)
        ).fetchone()
        assert row is not None  # the upsert above guarantees exactly one row
        return _checkin_from_row(row)

    def by_date(self, date: str) -> list[Checkin]:
        """Return the date's check-ins ordered by member id."""
        rows = self._conn.execute(
            _CHECKIN_COLUMNS + " WHERE date = ? ORDER BY member_id", (date,)
        ).fetchall()
        return [_checkin_from_row(row) for row in rows]


class SettingsRepo:
    """SQLite-backed settings store with module-level DEFAULTS fallback."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the repository to an open, migrated connection."""
        self._conn = conn

    def get(self, key: str) -> str:
        """Return the stored value for key, falling back to DEFAULTS when the key is absent."""
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        stored = row["value"] if row is not None else None
        return str(stored) if stored is not None else DEFAULTS[key]

    def set(self, key: str, value: str) -> None:
        """Insert or overwrite the stored value for key."""
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()


class KnowledgeRepo:
    """SQLite-backed knowledge cache with an external-content FTS5 index (D2).

    The sync owns ALL writes to knowledge/knowledge_fts: per-file reindex is
    FTS delete -> row delete -> row insert -> FTS insert, under the shared write
    lock, one commit. Drive is the record; every row is rebuildable from it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the repository to an open, migrated connection."""
        self._conn = conn

    def replace_file(
        self,
        *,
        file_id: str,
        path: str,
        title: str,
        modified_time: str,
        fetched_at: str,
        chunks: list[Chunk],
    ) -> int:
        """Rewrite one file's cache rows and FTS entries; return the chunk count.

        Any failure - not only sqlite3.Error, e.g. an unencodable value that fails at
        bind time - rolls the transaction back and re-raises (db.migrate's discipline),
        so a failed reindex leaves both tables untouched instead of leaving its deletes
        pending for the next commit on the shared connection to poison.
        """
        with WRITE_TRANSACTION_LOCK:
            try:
                self._conn.execute(
                    "DELETE FROM knowledge_fts WHERE rowid IN"
                    " (SELECT chunk_id FROM knowledge WHERE file_id = ?)",
                    (file_id,),
                )
                self._conn.execute("DELETE FROM knowledge WHERE file_id = ?", (file_id,))
                for chunk in chunks:
                    cursor = self._conn.execute(
                        "INSERT INTO knowledge (file_id, path, title, heading, body,"
                        " modified_time, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            file_id,
                            path,
                            title,
                            chunk.heading,
                            chunk.body,
                            modified_time,
                            fetched_at,
                        ),
                    )
                    self._conn.execute(
                        "INSERT INTO knowledge_fts(rowid, title, body) VALUES (?, ?, ?)",
                        (cursor.lastrowid, title, chunk.body),
                    )
                self._conn.commit()
            except BaseException:
                # Not only sqlite3.Error: a non-sqlite failure (e.g. UnicodeEncodeError
                # binding a lone surrogate) must roll back too, or the FTS-delete +
                # row-delete stay pending in an open transaction and the NEXT commit on
                # the shared connection silently drops a previously indexed file.
                self._conn.rollback()
                raise
        return len(chunks)

    def watermark(self) -> str | None:
        """Return MAX(modified_time) over cached rows (None when the cache is empty)."""
        row = self._conn.execute("SELECT MAX(modified_time) AS m FROM knowledge").fetchone()
        return None if row is None or row["m"] is None else str(row["m"])

    def search(self, query: str, limit: int) -> list[KnowledgeHit]:
        """FTS5 MATCH ordered by bm25 (title 10:1 over body); top limit hits.

        Raises KnowledgeSearchError when the query is not a valid FTS5 MATCH query
        (the tool layer translates that into an actionable ok:False result).
        """
        try:
            rows = self._conn.execute(
                "SELECT k.chunk_id AS chunk_id, k.file_id AS file_id, k.path AS path,"
                " k.title AS title, k.heading AS heading,"
                " bm25(knowledge_fts, 10.0, 1.0) AS bm25_rank"
                " FROM knowledge_fts JOIN knowledge AS k ON k.chunk_id = knowledge_fts.rowid"
                " WHERE knowledge_fts MATCH ?"
                " ORDER BY bm25_rank LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise KnowledgeSearchError(f"invalid FTS5 query {query!r}: {exc}") from exc
        return [
            KnowledgeHit(
                chunk_id=int(row["chunk_id"]),
                file_id=str(row["file_id"]),
                path=str(row["path"]),
                title=str(row["title"]),
                heading=_nullable_str(row, "heading"),
                rank=float(row["bm25_rank"]),
            )
            for row in rows
        ]
