"""Handler tests for knowledge_search (D2): the FTS5 finding-aid tool.

The sync that populates the cache is the deterministic script (v6.1, T2.18) — the
agent never ingests, so there is no knowledge_sync tool to test. These tests seed the
real SQLite KnowledgeRepo on tmp_path directly (the same replace_file path the script
uses) and exercise the search handler.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coordinator.db import connect, migrate
from coordinator.handlers import knowledge_search
from coordinator.knowledge import Chunk, chunk_markdown
from coordinator.repositories import KnowledgeRepo

FIXED_FETCHED_AT = "2026-09-04T12:00:00+00:00"


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


def _seed(
    knowledge: KnowledgeRepo,
    *,
    file_id: str,
    path: str,
    title: str,
    modified_time: str = "2026-09-01T00:00:00Z",
    content: str = "## A\nalpha\n",
) -> None:
    """Populate the cache through the same repository path the sync script uses."""
    chunks = (
        chunk_markdown(content) if content and content.strip() else [Chunk(heading=None, body="")]
    )
    knowledge.replace_file(
        file_id=file_id,
        path=path,
        title=title,
        modified_time=modified_time,
        fetched_at=FIXED_FETCHED_AT,
        chunks=chunks,
    )


# --- knowledge_search -------------------------------------------------------------------


def test_search_returns_top_chunks_with_locators(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """A hit carries exactly file_id/path/title/heading - no rank, no body leakage."""
    _seed(knowledge, file_id="f1", path="docs/a.md", title="A", content="## Findings\nalpha\n")

    result = knowledge_search({"query": "alpha"}, None, None, None, FakeClock(), knowledge)

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
    for index in range(11):
        _seed(
            knowledge,
            file_id=f"doc{index:02d}",
            path=f"docs/doc{index:02d}.md",
            title=f"Doc {index:02d} needle",
            content=f"## Doc {index:02d}\nneedle {index:02d}\n",
        )

    default = knowledge_search({"query": "needle"}, None, None, None, FakeClock(), knowledge)
    assert len(default["data"]["results"]) == 3

    capped = knowledge_search(
        {"query": "needle", "limit": 100}, None, None, None, FakeClock(), knowledge
    )
    assert len(capped["data"]["results"]) == 10

    two = knowledge_search(
        {"query": "needle", "limit": 2}, None, None, None, FakeClock(), knowledge
    )
    assert len(two["data"]["results"]) == 2


def test_search_malformed_query_fails_actionably(
    conn: sqlite3.Connection,
    knowledge: KnowledgeRepo,
) -> None:
    """A malformed MATCH query surfaces as ok:False, never as a raw sqlite error."""
    _seed(knowledge, file_id="f1", path="docs/a.md", title="A")

    result = knowledge_search({"query": "alpha AND ("}, None, None, None, FakeClock(), knowledge)

    assert result["ok"] is False
    assert result["cron_relay"] is None
    assert result["data"] == {}


def test_search_no_hits_is_ok_empty(conn: sqlite3.Connection, knowledge: KnowledgeRepo) -> None:
    """A well-formed query with no matches is ok:True with an empty result list."""
    result = knowledge_search({"query": "zzznothing"}, None, None, None, FakeClock(), knowledge)

    assert result["ok"] is True
    assert result["data"]["results"] == []


def test_search_ranks_title_hits_first(
    conn: sqlite3.Connection,
    knowledge: KnowledgeRepo,
) -> None:
    """Ranking through the tool layer: the title hit precedes the body-only hit."""
    _seed(
        knowledge,
        file_id="body-hit",
        path="docs/body.md",
        title="Unrelated title",
        content="## Notes\nexport export export export\n",
    )
    _seed(
        knowledge,
        file_id="title-hit",
        path="docs/title.md",
        title="Export pipeline",
        content="## Notes\nmentions export once\n",
    )

    result = knowledge_search({"query": "export"}, None, None, None, FakeClock(), knowledge)

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
