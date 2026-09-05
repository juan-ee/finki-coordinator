"""Handler tests for knowledge_sync (D2): the two-call, agent-mediated $GAPI sync.

The handler is pure: the agent performs the actual Drive I/O through the
google-workspace skill ($GAPI) — call 1 (plan) returns the watermark, call 2 (ingest)
stores the fetched files. These tests use the real SQLite KnowledgeRepo on tmp_path.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coordinator.db import connect, migrate
from coordinator.handlers import knowledge_search, knowledge_sync
from coordinator.repositories import KnowledgeRepo


class FakeClock:
    """Deterministic Clock: returns a fixed aware UTC instant."""

    def now(self) -> datetime:
        """Return the fixed instant."""
        return datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a migrated tmp_path database connection, closed after the test."""
    c = connect(tmp_path / "knowledge-tools.db")
    migrate(c, applied_at="2026-01-01T00:00:00+00:00")
    yield c
    c.close()


@pytest.fixture()
def knowledge(conn: sqlite3.Connection) -> KnowledgeRepo:
    """Build the real knowledge repository over the migrated connection."""
    return KnowledgeRepo(conn)


def _members_checkins_settings() -> tuple[None, None, None]:
    """The knowledge handlers use none of the member/checkin/settings deps."""
    return None, None, None


def test_plan_mode_returns_watermark_and_gapi_work_order(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """Call 1 (empty payload): the watermark plus the pre-computed $GAPI work order."""
    members, checkins, settings = _members_checkins_settings()

    result = knowledge_sync({}, members, checkins, settings, FakeClock(), knowledge)

    assert result["ok"] is True
    assert result["cron_relay"] is None
    assert result["data"]["watermark"] is None  # empty cache
    hint = result["data"]["files_hint"]
    assert "$GAPI" in hint
    assert "modifiedTime" in hint


def test_plan_mode_reports_the_stored_watermark_after_ingest(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """After an ingest, call 1 returns the advanced watermark for the next delta."""
    members, checkins, settings = _members_checkins_settings()
    files = [
        {
            "file_id": "f1",
            "path": "docs/a.md",
            "title": "A",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## A\nalpha\n",
        }
    ]
    first = knowledge_sync({"files": files}, members, checkins, settings, FakeClock(), knowledge)
    assert first["ok"] is True

    plan = knowledge_sync({}, members, checkins, settings, FakeClock(), knowledge)

    assert plan["ok"] is True
    assert plan["data"]["watermark"] == "2026-09-01T00:00:00Z"


def test_ingest_stores_chunks_and_reports_synced_count(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """Call 2: each file is chunked + stored; the result reports the count + watermark."""
    members, checkins, settings = _members_checkins_settings()
    files = [
        {
            "file_id": "f1",
            "path": "docs/a.md",
            "title": "A",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## One\nalpha\n## Two\nbeta\n",
        },
        {
            "file_id": "f2",
            "path": "docs/b.md",
            "title": "B",
            "modified_time": "2026-09-02T00:00:00Z",
            "content": "## B\ngamma\n",
        },
    ]

    result = knowledge_sync({"files": files}, members, checkins, settings, FakeClock(), knowledge)

    assert result["ok"] is True
    assert result["data"]["synced"] == 2
    assert result["data"]["watermark"] == "2026-09-02T00:00:00Z"
    hits = knowledge.search("alpha", limit=5)
    assert len(hits) == 1 and hits[0].file_id == "f1"


def test_ingest_non_text_file_stores_title_path_only_row(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """Audit second-pass rule: absent content = non-text -> one row, empty body."""
    members, checkins, settings = _members_checkins_settings()
    files = [
        {
            "file_id": "pdf1",
            "path": "docs/spec.pdf",
            "title": "The Spec",
            "modified_time": "2026-09-03T00:00:00Z",
        }
    ]

    result = knowledge_sync({"files": files}, members, checkins, settings, FakeClock(), knowledge)

    assert result["ok"] is True
    row = conn.execute("SELECT body, title, path FROM knowledge WHERE file_id = 'pdf1'").fetchone()
    assert row is not None
    assert row["body"] == ""
    assert row["title"] == "The Spec"
    # the title is still searchable
    hits = knowledge.search("spec", limit=5)
    assert len(hits) == 1 and hits[0].file_id == "pdf1"


def test_reingest_same_file_replaces_content_idempotently(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """Re-ingesting a file rewrites its rows (per-file reindex), never duplicates."""
    members, checkins, settings = _members_checkins_settings()
    first = [
        {
            "file_id": "f1",
            "path": "docs/a.md",
            "title": "A",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## Old\nold text\n",
        }
    ]
    second = [
        {
            "file_id": "f1",
            "path": "docs/a.md",
            "title": "A",
            "modified_time": "2026-09-04T00:00:00Z",
            "content": "## New\nnew text\n",
        }
    ]
    knowledge_sync({"files": first}, members, checkins, settings, FakeClock(), knowledge)

    result = knowledge_sync({"files": second}, members, checkins, settings, FakeClock(), knowledge)

    assert result["ok"] is True
    assert knowledge.search("old text", limit=5) == []
    assert len(knowledge.search("new text", limit=5)) == 1
    rows = list(conn.execute("SELECT count(*) AS n FROM knowledge WHERE file_id = 'f1'"))
    assert rows[0]["n"] == 1


def test_malformed_batch_rejects_without_partial_ingest(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """One malformed entry fails the WHOLE batch (validate-then-apply): nothing stored."""
    members, checkins, settings = _members_checkins_settings()
    files = [
        {
            "file_id": "good",
            "path": "docs/good.md",
            "title": "Good",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## Good\ngood\n",
        },
        {"file_id": "bad", "title": "missing path and modified_time"},
    ]

    result = knowledge_sync({"files": files}, members, checkins, settings, FakeClock(), knowledge)

    assert result["ok"] is False
    assert "path" in result["summary"]
    assert result["cron_relay"] is None
    assert knowledge.watermark() is None  # nothing ingested


def test_empty_ingest_is_a_noop(conn: sqlite3.Connection, knowledge: KnowledgeRepo) -> None:
    """An empty files list is ok: nothing synced, current watermark echoed."""
    members, checkins, settings = _members_checkins_settings()

    result = knowledge_sync({"files": []}, members, checkins, settings, FakeClock(), knowledge)

    assert result["ok"] is True
    assert result["data"]["synced"] == 0
    assert result["cron_relay"] is None


def test_ingest_rejects_non_list_files(knowledge: KnowledgeRepo) -> None:
    """files must be a list of entries; anything else fails actionably."""
    result = knowledge_sync({"files": "not-a-list"}, None, None, None, FakeClock(), knowledge)

    assert result["ok"] is False
    assert "files" in result["summary"]


def test_ingest_rejects_non_string_content(knowledge: KnowledgeRepo) -> None:
    """content, when present, must be a string (integers are a payload bug, not text)."""
    files = [
        {
            "file_id": "f1",
            "path": "docs/a.md",
            "title": "A",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": 5,
        }
    ]

    result = knowledge_sync({"files": files}, None, None, None, FakeClock(), knowledge)

    assert result["ok"] is False
    assert "content" in result["summary"]


def test_unknown_payload_keys_rejected(knowledge: KnowledgeRepo) -> None:
    """Only {} (plan) and {files} (ingest) are valid payloads."""
    result = knowledge_sync({"query": "x"}, None, None, None, FakeClock(), knowledge)

    assert result["ok"] is False
    assert "query" in result["summary"]


# --- knowledge_search -------------------------------------------------------------------


def test_search_returns_top_chunks_with_locators(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """A hit carries exactly file_id/path/title/heading - no rank, no body leakage."""
    members, checkins, settings = _members_checkins_settings()
    files = [
        {
            "file_id": "f1",
            "path": "docs/a.md",
            "title": "A",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## Findings\nalpha\n",
        }
    ]
    knowledge_sync({"files": files}, members, checkins, settings, FakeClock(), knowledge)

    result = knowledge_search(
        {"query": "alpha"}, members, checkins, settings, FakeClock(), knowledge
    )

    assert result["ok"] is True
    assert result["cron_relay"] is None
    assert result["data"]["query"] == "alpha"
    assert result["data"]["results"] == [
        {"file_id": "f1", "path": "docs/a.md", "title": "A", "heading": "Findings"}
    ]


def test_search_query_is_required(knowledge: KnowledgeRepo) -> None:
    """An absent or empty query fails actionably."""
    for payload in ({}, {"query": ""}):
        result = knowledge_search(payload, None, None, None, FakeClock(), knowledge)
        assert result["ok"] is False, payload
        assert "query" in result["summary"], payload


def test_search_limit_defaults_to_three_and_caps_at_ten(
    conn: sqlite3.Connection,
    knowledge: KnowledgeRepo,
) -> None:
    """limit defaults to 3 and is capped at 10 (a huge limit cannot flood the context)."""
    members, checkins, settings = _members_checkins_settings()
    files = [
        {
            "file_id": f"doc{index:02d}",
            "path": f"docs/doc{index:02d}.md",
            "title": f"Doc {index:02d} needle",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": f"## Doc {index:02d}\nneedle {index:02d}\n",
        }
        for index in range(11)
    ]
    knowledge_sync({"files": files}, members, checkins, settings, FakeClock(), knowledge)

    default = knowledge_search(
        {"query": "needle"}, members, checkins, settings, FakeClock(), knowledge
    )
    assert len(default["data"]["results"]) == 3

    capped = knowledge_search(
        {"query": "needle", "limit": 100}, members, checkins, settings, FakeClock(), knowledge
    )
    assert len(capped["data"]["results"]) == 10

    two = knowledge_search(
        {"query": "needle", "limit": 2}, members, checkins, settings, FakeClock(), knowledge
    )
    assert len(two["data"]["results"]) == 2


def test_search_malformed_query_fails_actionably(
    conn: sqlite3.Connection,
    knowledge: KnowledgeRepo,
) -> None:
    """A malformed MATCH query surfaces as ok:False, never as a raw sqlite error."""
    members, checkins, settings = _members_checkins_settings()
    files = [
        {
            "file_id": "f1",
            "path": "docs/a.md",
            "title": "A",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## A\nalpha\n",
        }
    ]
    knowledge_sync({"files": files}, members, checkins, settings, FakeClock(), knowledge)

    result = knowledge_search(
        {"query": "alpha AND ("}, members, checkins, settings, FakeClock(), knowledge
    )

    assert result["ok"] is False
    assert result["cron_relay"] is None
    assert result["data"] == {}


def test_search_no_hits_is_ok_empty(conn: sqlite3.Connection, knowledge: KnowledgeRepo) -> None:
    """A well-formed query with no matches is ok:True with an empty result list."""
    members, checkins, settings = _members_checkins_settings()

    result = knowledge_search(
        {"query": "zzznothing"}, members, checkins, settings, FakeClock(), knowledge
    )

    assert result["ok"] is True
    assert result["data"]["results"] == []


def test_search_ranks_title_hits_first(
    conn: sqlite3.Connection,
    knowledge: KnowledgeRepo,
) -> None:
    """Ranking through the tool layer: the title hit precedes the body-only hit."""
    members, checkins, settings = _members_checkins_settings()
    files = [
        {
            "file_id": "body-hit",
            "path": "docs/body.md",
            "title": "Unrelated title",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## Notes\nexport export export export\n",
        },
        {
            "file_id": "title-hit",
            "path": "docs/title.md",
            "title": "Export pipeline",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## Notes\nmentions export once\n",
        },
    ]
    knowledge_sync({"files": files}, members, checkins, settings, FakeClock(), knowledge)

    result = knowledge_search(
        {"query": "export"}, members, checkins, settings, FakeClock(), knowledge
    )

    assert [entry["file_id"] for entry in result["data"]["results"]] == [
        "title-hit",
        "body-hit",
    ]


def test_search_rejects_unknown_keys_and_bad_limit(knowledge: KnowledgeRepo) -> None:
    """Unknown payload keys fail; a non-integer or boolean limit fails typed."""
    for payload in (
        {"query": "x", "foo": 1},
        {"query": "x", "limit": "3"},
        {"query": "x", "limit": True},
    ):
        result = knowledge_search(payload, None, None, None, FakeClock(), knowledge)
        assert result["ok"] is False, payload
