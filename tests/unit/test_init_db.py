"""init_db CLI contract: fresh seed inserts N members, re-run is a no-op, no seed = empty."""

import pathlib
import sqlite3
import subprocess
import sys

import yaml

SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "init_db.py"

SEED_MEMBERS = [
    {
        "name": "Alice",
        "telegram_id": 101,
        "timezone": "America/Guayaquil",
        "wake": "08:00",
        "role": "backend",
    },
    {"name": "Bob", "timezone": "Europe/Berlin", "wake": "08:30"},
    {"name": "Carla", "telegram_id": 103, "role": "frontend"},
    {"name": "Dave"},
]


def _run_init_db(*args: str) -> subprocess.CompletedProcess[str]:
    """Run scripts/init_db.py with the venv python that is running pytest (offline, deterministic)."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_seed(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write the four-member seed fixture to a tmp YAML file and return its path."""
    seed_path = tmp_path / "members.seed.yaml"
    seed_path.write_text(yaml.safe_dump(SEED_MEMBERS), encoding="utf-8")
    return seed_path


def _member_count(db_path: pathlib.Path) -> int:
    """Count rows in the members table of the given database."""
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM members").fetchone()[0])
    finally:
        conn.close()


def _seeded_at(db_path: pathlib.Path) -> str | None:
    """Return the stored seeded_at setting, or None when the marker is absent."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'seeded_at'").fetchone()
    finally:
        conn.close()
    return None if row is None else str(row[0])


def test_fresh_seed_inserts_all_members(tmp_path: pathlib.Path) -> None:
    """First run with --seed inserts every seed member and stamps the seeded_at marker."""
    db_path = tmp_path / "hermes-coord.db"
    seed_path = _write_seed(tmp_path)

    result = _run_init_db("--db", str(db_path), "--seed", str(seed_path))

    assert result.returncode == 0, result.stderr
    assert _member_count(db_path) == len(SEED_MEMBERS)
    assert _seeded_at(db_path) is not None


def test_second_run_does_not_duplicate(tmp_path: pathlib.Path) -> None:
    """Re-running with the same --seed skips: same count, unchanged marker, one-line note."""
    db_path = tmp_path / "hermes-coord.db"
    seed_path = _write_seed(tmp_path)
    first = _run_init_db("--db", str(db_path), "--seed", str(seed_path))
    marker_before = _seeded_at(db_path)
    assert first.returncode == 0, first.stderr

    second = _run_init_db("--db", str(db_path), "--seed", str(seed_path))

    assert second.returncode == 0, second.stderr
    assert _member_count(db_path) == len(SEED_MEMBERS)
    assert _seeded_at(db_path) == marker_before
    assert "seeded_at" in second.stdout


def test_run_without_seed_leaves_members_empty(tmp_path: pathlib.Path) -> None:
    """Without --seed the CLI only creates and migrates the database: zero members, no marker."""
    db_path = tmp_path / "hermes-coord.db"

    result = _run_init_db("--db", str(db_path))

    assert result.returncode == 0, result.stderr
    assert _member_count(db_path) == 0
    assert _seeded_at(db_path) is None


def test_malformed_seed_fails_without_marker(tmp_path: pathlib.Path) -> None:
    """A malformed seed exits non-zero, inserts nothing, and leaves no marker behind."""
    db_path = tmp_path / "hermes-coord.db"
    seed_path = tmp_path / "bad.seed.yaml"
    seed_path.write_text("just a string\n", encoding="utf-8")

    result = _run_init_db("--db", str(db_path), "--seed", str(seed_path))

    assert result.returncode != 0
    assert _member_count(db_path) == 0
    assert _seeded_at(db_path) is None
    assert result.stderr != ""
