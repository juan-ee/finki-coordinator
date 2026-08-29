#!/usr/bin/env python3
"""Initialize the coordinator SQLite database: mkdir, connect, migrate, and seed members once.

Impure shell (AGENTS.md rule 3): this CLI owns all I/O and stamps wall-clock UTC ISO
instants (AGENTS.md rule 5 sanctions wall-clock stamping in scripts/). One instant is
used for the migration timestamp, every member's created_at, and the seeded_at marker.
Seeding is all-or-nothing by pre-validation: the whole seed file (shape, field types,
telegram_id uniqueness within the seed and against existing rows) is validated BEFORE
any insert, so a malformed seed cannot leave partial rows; the marker is written last,
only after every member is stored.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from coordinator.db import connect, migrate
from coordinator.repositories import MembersRepo, SettingsRepo

SEED_MARKER = "seeded_at"

_OPTIONAL_STR_FIELDS = ("timezone", "wake", "role")

_EPILOG = """seed file shape (YAML list of member dicts):

  - name: Alice                # required, non-empty
    telegram_id: 101           # optional integer
    timezone: Europe/Berlin    # optional IANA name (default UTC)
    wake: "08:30"              # optional 'HH:MM' local wake time
    role: backend              # optional free text

Missing optional fields are tolerated. Seeding is all-or-nothing: the seed file is
fully validated before any insert and the 'seeded_at' settings marker is written
only after every member is stored, so re-running with the same --seed is a no-op."""


def _validated_entries(raw: object) -> list[dict[str, object]]:
    """Return seed entries as MembersRepo.add kwargs, rejecting any malformed member row."""
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        raise ValueError("seed file must be a YAML list of member mappings")
    entries: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    for index, entry in enumerate(raw, start=1):
        where = f"seed member #{index}"
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}: 'name' is required and must be a non-empty string")
        kwargs: dict[str, object] = {"name": name}
        telegram_id = entry.get("telegram_id")
        if telegram_id is not None:
            if isinstance(telegram_id, bool) or not isinstance(telegram_id, int):
                raise ValueError(f"{where}: 'telegram_id' must be an integer")
            if telegram_id in seen_ids:
                raise ValueError(f"{where}: duplicate telegram_id {telegram_id} in seed file")
            seen_ids.add(telegram_id)
            kwargs["telegram_id"] = telegram_id
        for field in _OPTIONAL_STR_FIELDS:
            value = entry.get(field)
            if not value:
                continue  # missing/empty optional field: leave it to the repo default
            if not isinstance(value, str):
                raise TypeError(f"{where}: '{field}' must be a string")
            kwargs[field] = value
        entries.append(kwargs)
    return entries


def _db_telegram_id_conflicts(
    conn: sqlite3.Connection, entries: list[dict[str, object]]
) -> set[int]:
    """Return seed telegram_ids that already exist in the members table."""
    wanted = sorted({int(entry["telegram_id"]) for entry in entries if "telegram_id" in entry})
    if not wanted:
        return set()
    placeholders = ", ".join("?" * len(wanted))
    rows = conn.execute(
        f"SELECT telegram_id FROM members WHERE telegram_id IN ({placeholders})", wanted
    )
    return {int(row["telegram_id"]) for row in rows}


def main(argv: list[str] | None = None) -> int:
    """Create/migrate the database at --db, optionally seeding members once from --seed."""
    parser = argparse.ArgumentParser(
        description="Initialize the coordinator SQLite database (mkdir, migrate, seed once).",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, metavar="PATH", help="SQLite database path")
    parser.add_argument(
        "--seed",
        metavar="SEED_YAML",
        help="optional members seed YAML to import once (see below); default: no seeding",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    now = datetime.now(UTC).isoformat()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        migrate(conn, applied_at=now)
        members = MembersRepo(conn)
        settings = SettingsRepo(conn)
        if args.seed is None:
            return 0
        try:
            marker = settings.get(SEED_MARKER)
        except KeyError:  # seeded_at is not a DEFAULTS knob: absent key means unseeded
            marker = None
        if marker is not None:
            print(f"{SEED_MARKER} already present ({marker}); skipping seed")
            return 0
        raw = yaml.safe_load(Path(args.seed).read_text(encoding="utf-8"))
        entries = _validated_entries(raw)
        conflicts = sorted(_db_telegram_id_conflicts(conn, entries))
        if conflicts:
            raise ValueError(f"telegram_id already present in members table: {conflicts}")
        for kwargs in entries:
            members.add(created_at=now, **kwargs)
        settings.set(SEED_MARKER, now)
    except (OSError, TypeError, ValueError, yaml.YAMLError, sqlite3.Error) as error:
        print(f"error: seeding failed: {error}", file=sys.stderr)
        print(
            f"no '{SEED_MARKER}' marker was written; fix the problem and re-run this command",
            file=sys.stderr,
        )
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
