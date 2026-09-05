"""Read-through freshness gate tests (T2.23): TTL edges, debounce, degraded mode, round-trip.

The gate rides knowledge_search: before matching, the handler asks the wired
KnowledgeFreshness seam whether the cache is due for a refresh (pure TTL decision on
an injected Clock); a due search runs the deterministic incremental sync (T2.18
engine) first. A Drive outage must never take down reading (degraded mode). The
round-trip test is the real freshness proof: a Drive-side edit becomes findable
within one search after the TTL elapses.
"""

import sqlite3
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from coordinator.db import connect, migrate
from coordinator.handlers import FreshnessOutcome, freshness_due, knowledge_search
from coordinator.hermes_plugin import FreshnessGate, dispatch
from coordinator.knowledge import chunk_markdown
from coordinator.repositories import KnowledgeRepo

NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """Deterministic Clock with a settable instant."""

    def __init__(self, instant: datetime = NOW) -> None:
        self.instant = instant

    def now(self) -> datetime:
        """Return the set instant."""
        return self.instant


class FakeGate:
    """KnowledgeFreshness fake: records refresh calls, returns a canned outcome."""

    def __init__(
        self,
        *,
        ttl_minutes: int = 10,
        last_check: str | None = None,
        outcome: FreshnessOutcome | None = None,
        error: Exception | None = None,
    ) -> None:
        self._ttl = ttl_minutes
        self._last_check = last_check
        self._outcome = outcome or FreshnessOutcome("refreshed", "ingested 1 file(s)")
        self._error = error
        self.refresh_calls: list[str] = []

    def ttl_minutes(self) -> int:
        """Return the wired TTL."""
        return self._ttl

    def last_check(self) -> str | None:
        """Return the stored last-freshness-check instant."""
        return self._last_check

    def refresh(self, now: str) -> FreshnessOutcome:
        """Record the attempt and return the canned outcome (or raise the canned error)."""
        self.refresh_calls.append(now)
        if self._error is not None:
            raise self._error
        return self._outcome


class RoundTripGate:
    """Fake gate whose refresh mimics the script: ingests the Drive-side edit for real."""

    def __init__(self, knowledge: KnowledgeRepo, *, ttl_minutes: int = 10) -> None:
        self._knowledge = knowledge
        self._ttl = ttl_minutes
        self._last_check: str | None = None
        self.refresh_calls = 0

    def ttl_minutes(self) -> int:
        """Return the wired TTL."""
        return self._ttl

    def last_check(self) -> str | None:
        """Return the stored last-freshness-check instant."""
        return self._last_check

    def refresh(self, now: str) -> FreshnessOutcome:
        """Ingest the Drive-side edit through the same repository path the script uses."""
        self.refresh_calls += 1
        self._knowledge.replace_file(
            file_id="new-doc",
            path="docs/new.md",
            title="New brief",
            modified_time="2026-09-06T00:00:00Z",
            fetched_at=now,
            chunks=chunk_markdown("## New\nbrandonite marker\n"),
        )
        self._last_check = now  # the gate stamps the attempt
        return FreshnessOutcome("refreshed", "ingested 1 file(s); failed 0")


@pytest.fixture()
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a migrated tmp_path database connection, closed after the test."""
    c = connect(tmp_path / "freshness.db")
    migrate(c, applied_at="2026-01-01T00:00:00+00:00")
    yield c
    c.close()


@pytest.fixture()
def knowledge(conn: sqlite3.Connection) -> KnowledgeRepo:
    """The real knowledge repository over the migrated connection."""
    return KnowledgeRepo(conn)


def _iso(minutes_before_now: int) -> str:
    """An ISO stamp the given number of minutes before NOW."""
    return (NOW - timedelta(minutes=minutes_before_now)).isoformat()


def _seed(knowledge: KnowledgeRepo) -> None:
    """Seed one cached document (the pre-edit cache state)."""
    knowledge.replace_file(
        file_id="old-doc",
        path="docs/old.md",
        title="Old brief",
        modified_time="2026-09-01T00:00:00Z",
        fetched_at="2026-09-01T00:00:00+00:00",
        chunks=chunk_markdown("## Old\nold marker\n"),
    )


# --- freshness_due: the pure TTL decision ----------------------------------------------------


def test_freshness_due_when_never_checked() -> None:
    """No stored stamp (fresh install) means due."""
    assert freshness_due(None, NOW, 10) is True


def test_freshness_due_inside_the_ttl_window_is_false() -> None:
    """Debounce: a search 5 minutes into a 10-minute TTL does not re-check."""
    assert freshness_due(_iso(5), NOW, 10) is False


def test_freshness_due_exactly_at_the_ttl_is_true() -> None:
    """The TTL boundary is inclusive: at exactly ttl minutes the cache is due."""
    assert freshness_due(_iso(10), NOW, 10) is True


def test_freshness_due_past_the_ttl_is_true() -> None:
    """Past the window the cache is due."""
    assert freshness_due(_iso(11), NOW, 10) is True


def test_freshness_due_with_unparseable_stamp_is_true() -> None:
    """A corrupt stamp cannot prove freshness: due (fail-open to refresh)."""
    assert freshness_due("not-a-timestamp", NOW, 10) is True


def test_freshness_due_treats_naive_stamp_as_utc() -> None:
    """A naive stored stamp is read as UTC (the cache is UTC-anchored), not host-local."""
    naive = "2026-09-06T11:55:00"  # 5 minutes before NOW, no offset
    assert freshness_due(naive, NOW, 10) is False


# --- knowledge_search with the gate wired -----------------------------------------------------


def test_search_inside_the_ttl_debounces_and_never_refreshes(
    knowledge: KnowledgeRepo,
) -> None:
    """A search inside the TTL window never re-checks: refresh is not called."""
    _seed(knowledge)
    gate = FakeGate(last_check=_iso(1))

    result = knowledge_search(
        {"query": "old marker"}, None, None, None, FakeClock(), knowledge, gate
    )

    assert result["ok"] is True
    assert gate.refresh_calls == []
    assert "cache refreshed" not in result["summary"]


def test_search_past_the_ttl_refreshes_once_then_proceeds(
    knowledge: KnowledgeRepo,
) -> None:
    """A due search runs the refresh first, then searches; the summary says so."""
    _seed(knowledge)
    gate = FakeGate(last_check=_iso(30))

    result = knowledge_search(
        {"query": "old marker"}, None, None, None, FakeClock(), knowledge, gate
    )

    assert result["ok"] is True
    assert gate.refresh_calls == [NOW.isoformat()]
    assert "cache refreshed" in result["summary"]
    assert [h["file_id"] for h in result["data"]["results"]] == ["old-doc"]


def test_search_with_no_stored_stamp_refreshes(knowledge: KnowledgeRepo) -> None:
    """Never-checked caches are due: the first search refreshes."""
    _seed(knowledge)
    gate = FakeGate(last_check=None)

    knowledge_search({"query": "old marker"}, None, None, None, FakeClock(), knowledge, gate)

    assert len(gate.refresh_calls) == 1


def test_degraded_mode_serves_the_cache_and_says_so(knowledge: KnowledgeRepo) -> None:
    """Drive unreachable: the refresh fails but the search still serves cached hits,
    with the degradation visible in the summary (reading never hard-fails)."""
    _seed(knowledge)
    gate = FakeGate(
        last_check=_iso(30),
        outcome=FreshnessOutcome("degraded", "gapi drive search failed (exit 1): quota"),
    )

    result = knowledge_search(
        {"query": "old marker"}, None, None, None, FakeClock(), knowledge, gate
    )

    assert result["ok"] is True  # reading never hard-fails
    assert [h["file_id"] for h in result["data"]["results"]] == ["old-doc"]
    assert "freshness check FAILED" in result["summary"]
    assert "quota" in result["summary"]
    assert "possibly-stale" in result["summary"]


def test_refresh_crash_degrades_instead_of_failing_the_search(
    knowledge: KnowledgeRepo,
) -> None:
    """An unexpected refresh crash degrades the round: the search still serves."""
    _seed(knowledge)
    gate = FakeGate(last_check=_iso(30), error=RuntimeError("boom"))

    result = knowledge_search(
        {"query": "old marker"}, None, None, None, FakeClock(), knowledge, gate
    )

    assert result["ok"] is True
    assert "freshness check FAILED" in result["summary"]
    assert "boom" in result["summary"]


def test_control_character_query_never_triggers_a_refresh(
    knowledge: KnowledgeRepo,
) -> None:
    """Payload validation runs BEFORE the gate: a rejected query must not cause a sync.
    (FTS5-syntax errors are search-time and deliberately post-gate.)"""
    _seed(knowledge)
    gate = FakeGate(last_check=_iso(30))

    result = knowledge_search(
        {"query": "alpha" + chr(0) + "b"}, None, None, None, FakeClock(), knowledge, gate
    )

    assert result["ok"] is False
    assert gate.refresh_calls == []


def test_search_without_a_wired_gate_is_unchanged(knowledge: KnowledgeRepo) -> None:
    """Back-compat: no freshness dep wired means today's behavior, no refresh machinery."""
    _seed(knowledge)

    result = knowledge_search({"query": "old marker"}, None, None, None, FakeClock(), knowledge)

    assert result["ok"] is True
    assert "cache refreshed" not in result["summary"]


# --- the freshness round-trip: edit -> gate -> findable ---------------------------------------


def test_drive_side_edit_becomes_findable_within_one_search_after_ttl(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """The proper freshness test: cache is stale, a doc is edited on Drive, the FIRST
    search after the TTL elapses refreshes and the edit is findable in that search's
    results; the next search debounces."""
    _seed(knowledge)
    gate = RoundTripGate(knowledge, ttl_minutes=10)
    stale_clock = FakeClock(NOW)  # last_check None -> due immediately

    first = knowledge_search(
        {"query": "brandonite"}, None, None, None, stale_clock, knowledge, gate
    )

    assert first["ok"] is True
    assert gate.refresh_calls == 1
    assert [h["file_id"] for h in first["data"]["results"]] == ["new-doc"], (
        "the Drive-side edit must be findable in the very search that triggered the refresh"
    )

    later_clock = FakeClock(NOW + timedelta(minutes=1))
    second = knowledge_search(
        {"query": "brandonite"}, None, None, None, later_clock, knowledge, gate
    )
    assert gate.refresh_calls == 1  # debounce: inside the TTL no re-check
    assert [h["file_id"] for h in second["data"]["results"]] == ["new-doc"]


# --- dispatch wiring: the freshness dep rides the takes_knowledge path ------------------------


def test_dispatch_passes_the_wired_freshness_gate(knowledge: KnowledgeRepo) -> None:
    """dispatch() hands the wired gate to the knowledge tool (wiring contract)."""
    _seed(knowledge)
    gate = FakeGate(last_check=_iso(30))

    result = dispatch(
        "knowledge_search",
        {"query": "old marker"},
        members=None,
        checkins=None,
        settings=None,
        clock=FakeClock(),
        knowledge=knowledge,
        freshness=gate,
    )

    assert result["ok"] is True
    assert gate.refresh_calls == [NOW.isoformat()]


# --- the concrete FreshnessGate (subprocess + stamping) ---------------------------------------


def _gate(tmp_path: Path, script_stdout: str = "") -> FreshnessGate:
    """A concrete gate over a migrated tmp DB and a fake script path."""
    conn = connect(tmp_path / "h.db")
    migrate(conn, applied_at="2026-01-01T00:00:00+00:00")
    conn.close()
    return FreshnessGate(
        ttl_minutes=7,
        script_path=tmp_path / "sync_knowledge.py",
        db_path=tmp_path / "h.db",
    )


def _stamp(db_path: Path) -> str | None:
    """Read the stored freshness stamp through a fresh connection."""
    row = (
        sqlite3.connect(db_path)
        .execute("SELECT value FROM settings WHERE key = 'knowledge_last_freshness_check'")
        .fetchone()
    )
    return None if row is None else str(row[0])


def test_gate_last_check_is_none_before_any_attempt(tmp_path: Path) -> None:
    """No attempt yet: last_check is None (due)."""
    gate = _gate(tmp_path)

    assert gate.last_check() is None
    assert gate.ttl_minutes() == 7


def test_gate_refresh_runs_the_script_and_stamps_the_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh runs the sync script (stdin detached, bounded) and stamps now."""
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured.update(kwargs)
        return type(
            "R", (), {"returncode": 0, "stdout": "ingested 2 file(s); watermark X", "stderr": ""}
        )()

    monkeypatch.setattr("coordinator.hermes_plugin.subprocess.run", fake_run)
    gate = _gate(tmp_path)

    outcome = gate.refresh(NOW.isoformat())

    assert outcome.status == "refreshed"
    assert "ingested 2 file(s)" in outcome.detail
    argv = captured["argv"]
    assert argv[-1] == str(tmp_path / "sync_knowledge.py")
    assert captured["stdin"] is not None  # stdin detached (never inherit)
    assert captured["timeout"] == 120
    assert _stamp(tmp_path / "h.db") == NOW.isoformat()


def test_gate_refresh_failure_degrades_and_still_stamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed script run degrades (reason surfaced) and the attempt is stamped —
    debounce must hold during a Drive outage."""

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom quota"})()

    monkeypatch.setattr("coordinator.hermes_plugin.subprocess.run", fake_run)
    gate = _gate(tmp_path)

    outcome = gate.refresh(NOW.isoformat())

    assert outcome.status == "degraded"
    assert "quota" in outcome.detail
    assert _stamp(tmp_path / "h.db") == NOW.isoformat()


def test_gate_refresh_timeout_degrades_and_still_stamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung script hits the subprocess timeout: degraded, stamped, search served."""

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd=argv, timeout=120)

    monkeypatch.setattr("coordinator.hermes_plugin.subprocess.run", fake_run)
    gate = _gate(tmp_path)

    outcome = gate.refresh(NOW.isoformat())

    assert outcome.status == "degraded"
    assert "timed out" in outcome.detail
    assert _stamp(tmp_path / "h.db") == NOW.isoformat()
