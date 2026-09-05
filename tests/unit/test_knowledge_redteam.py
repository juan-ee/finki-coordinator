"""Phase-gate red-team adoptions (T2.17): the knowledge-subsystem attack regressions.

Adopted from the phase-gate red team's adversarial battery (AGENTS.md review
protocol step 4, run at HEAD ddfbe54 ahead of the T2.17 manual gate; battery:
/tmp/redteam_knowledge.py, 13 failed / 31 passed). Every test here encodes a real
bug the red team broke open - fixed in the same change as this module - and pins
the FIXED behavior as a regression test; the red team's attack names keep a
redteam_ prefix. Bug map: B1 replace_file rolls back on ANY failure, not only
sqlite3.Error (tests 1-3); B2 knowledge_search rejects NUL/C0 control-character
queries up front (test 4); B3 a fence closes only on the same character and an
at-least-as-long run (tests 5-6); B4 only CRLF/CR/LF are markdown line endings
(tests 7-11); B5 an empty text file stores a searchable title/path-only row and
advances the watermark (test 12); B6 ingested modified_time values are
canonicalized to UTC so the watermark is chronological (test 13). Deterministic:
tmp_path, fixed clock, no network.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coordinator.db import connect, migrate
from coordinator.handlers import knowledge_search, knowledge_sync
from coordinator.knowledge import Chunk, chunk_markdown
from coordinator.repositories import KnowledgeRepo

FIXED_FETCHED_AT = "2026-09-04T10:00:00Z"


class FakeClock:
    """Deterministic Clock: a fixed aware UTC instant."""

    def now(self) -> datetime:
        return datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a migrated tmp_path connection, closed after the test."""
    c = connect(tmp_path / "redteam.db")
    migrate(c, applied_at="2026-01-01T00:00:00+00:00")
    yield c
    c.close()


@pytest.fixture()
def knowledge(conn: sqlite3.Connection) -> KnowledgeRepo:
    """The real knowledge repository over the migrated connection."""
    return KnowledgeRepo(conn)


# --- B1: a non-sqlite3.Error failure must still roll back replace_file --------------------


def test_redteam_replace_file_non_sqlite_failure_rolls_back(conn: sqlite3.Connection) -> None:
    """A bind failure that is NOT sqlite3.Error (a lone surrogate - JSON \\ud800 decodes
    to one) must still roll back: the file's rows stay intact, no open transaction."""
    repo = KnowledgeRepo(conn)
    repo.replace_file(
        file_id="victim",
        path="docs/v.md",
        title="Victim",
        modified_time="2026-09-01T00:00:00Z",
        fetched_at=FIXED_FETCHED_AT,
        chunks=[Chunk(heading=None, body="victimword alpha")],
    )
    # the bind error itself is fine (the finding: "a ValueError, NOT sqlite3.Error");
    # the state below was the bug
    with pytest.raises(UnicodeEncodeError):
        repo.replace_file(
            file_id="victim",
            path="docs/v.md",
            title="Victim",
            modified_time="2026-09-02T00:00:00Z",
            fetched_at=FIXED_FETCHED_AT,
            chunks=[Chunk(heading=None, body="replacement \ud800 text")],
        )
    assert conn.in_transaction is False, "aborted reindex left an open write transaction"
    assert len(repo.search("victimword", 5)) == 1, "aborted reindex lost the file's rows"


def test_redteam_failed_reindex_survives_later_unrelated_sync(conn: sqlite3.Connection) -> None:
    """The open transaction from a failed reindex must not be committed by the NEXT
    successful write on the shared connection (poisoning an unrelated file's sync)."""
    repo = KnowledgeRepo(conn)
    repo.replace_file(
        file_id="victim",
        path="docs/v.md",
        title="Victim",
        modified_time="2026-09-01T00:00:00Z",
        fetched_at=FIXED_FETCHED_AT,
        chunks=[Chunk(heading=None, body="victimword")],
    )
    # the failure is the setup; the poisoning below was the bug
    with pytest.raises(UnicodeEncodeError):
        repo.replace_file(
            file_id="victim",
            path="docs/v.md",
            title="Victim",
            modified_time="2026-09-02T00:00:00Z",
            fetched_at=FIXED_FETCHED_AT,
            chunks=[Chunk(heading=None, body="new \ud800 text")],
        )
    repo.replace_file(
        file_id="other",
        path="docs/o.md",
        title="Other",
        modified_time="2026-09-03T00:00:00Z",
        fetched_at=FIXED_FETCHED_AT,
        chunks=[Chunk(heading=None, body="otherword beta")],
    )
    assert len(repo.search("victimword", 5)) == 1, (
        "the later unrelated sync committed the aborted reindex's deletes"
    )


def test_redteam_knowledge_sync_undecodable_content_fails_cleanly(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """A batch entry with undecodable content (a lone surrogate) must leave the cache
    consistent. Adjusted from the red team's ok:False expectation: content encoding
    is not payload validation, so the bind failure still propagates out of the
    handler - but B1's rollback means no open transaction and no rows for the
    failing file; the earlier valid entry stays stored (per-file reindex commits)."""
    files = [
        {
            "file_id": "good",
            "path": "docs/g.md",
            "title": "Good",
            "modified_time": "2026-09-01T00:00:00Z",
            "content": "## Good\ngoodword",
        },
        {
            "file_id": "bad",
            "path": "docs/b.md",
            "title": "Bad",
            "modified_time": "2026-09-02T00:00:00Z",
            "content": "payload \ud800 content",
        },
    ]
    with pytest.raises(UnicodeEncodeError):
        knowledge_sync({"files": files}, None, None, None, FakeClock(), knowledge)
    assert conn.in_transaction is False, "aborted store left an open write transaction"
    rows = conn.execute("SELECT file_id FROM knowledge ORDER BY file_id").fetchall()
    assert [str(row["file_id"]) for row in rows] == ["good"], (
        "the failing entry must leave no rows; the earlier valid entry stays stored"
    )


# --- B2: an embedded NUL must not silently truncate the MATCH query -----------------------


def test_redteam_search_rejects_embedded_nul_query(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """knowledge_search rejects queries containing NUL or any other C0 control
    character with an actionable ok:False BEFORE the repo call: FTS5 MATCH silently
    truncates at an embedded NUL, so 'alpha\\x00nomatchxyz' would match plain 'alpha'."""
    repo = KnowledgeRepo(conn)
    repo.replace_file(
        file_id="n1",
        path="docs/n.md",
        title="N",
        modified_time="2026-09-01T00:00:00Z",
        fetched_at=FIXED_FETCHED_AT,
        chunks=[Chunk(heading=None, body="alpha uniquebodyword")],
    )
    result = knowledge_search(
        {"query": "alpha\x00nomatchxyz"}, None, None, None, FakeClock(), knowledge
    )
    assert result["ok"] is False
    assert result["cron_relay"] is None
    assert result["data"] == {}
    # the guard rejects EVERY C0 control character (< 0x20), not just NUL
    for code in range(0x20):
        rejected = knowledge_search(
            {"query": f"a{chr(code)}b"}, None, None, None, FakeClock(), knowledge
        )
        assert rejected["ok"] is False, f"U+{code:04X} must be rejected"
    # control: the plain pre-NUL prefix still matches normally
    control = knowledge_search({"query": "alpha"}, None, None, None, FakeClock(), knowledge)
    assert control["ok"] is True
    assert [hit["file_id"] for hit in control["data"]["results"]] == ["n1"]


# --- B3: fence toggling must respect fence type and run length ----------------------------


def test_redteam_backtick_fence_is_not_closed_by_a_tilde_line() -> None:
    """A ~~~ line inside a ``` fence is content: the fenced '## Fake' line stays
    body text and does not split a phantom chunk out."""
    chunks = chunk_markdown("## Real\n```\n~~~\n## Fake\n```\ntail")
    assert len(chunks) == 1
    assert chunks[0].heading == "Real"
    assert "## Fake" in chunks[0].body


def test_redteam_longer_backtick_fence_is_not_closed_by_a_shorter_run() -> None:
    """A four-backtick fence is not closed by a three-backtick line (the closing run
    must be at least as long): the heading after the real closer splits as its own chunk."""
    chunks = chunk_markdown("## Intro\n````\ncode\n```\n````\n## Next\nnext body")
    assert [c.heading for c in chunks] == ["Intro", "Next"]


# --- B4: only CRLF/CR/LF are markdown line endings -----------------------------------------


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85", "\x0b", "\x0c"])
def test_redteam_unicode_separators_are_not_markdown_line_endings(sep: str) -> None:
    """CommonMark line endings are LF, CR, CRLF only: U+2028/U+2029/NEL/VT/FF are
    mid-line content, so a '## ' after one is not at line start and must not split."""
    chunks = chunk_markdown(f"before{sep}## Fake\nafter")
    assert len(chunks) == 1
    assert chunks[0].heading is None
    assert "## Fake" in chunks[0].body


# --- B5: an empty text file must leave a cache trace ---------------------------------------


def test_redteam_empty_text_file_keeps_searchable_title_and_advances_watermark(
    knowledge: KnowledgeRepo,
) -> None:
    """content='' stores one title/path-only row (like a non-text file), so the title
    stays searchable and the watermark advances past the file's modified_time."""
    result = knowledge_sync(
        {
            "files": [
                {
                    "file_id": "empty1",
                    "path": "docs/empty.md",
                    "title": "Vacant Report",
                    "modified_time": "2026-09-05T00:00:00Z",
                    "content": "",
                }
            ]
        },
        None,
        None,
        None,
        FakeClock(),
        knowledge,
    )
    assert result["data"]["watermark"] == "2026-09-05T00:00:00Z"
    assert [h.file_id for h in knowledge.search("vacant", 5)] == ["empty1"]


# --- B6: the watermark must be chronological, not lexicographic ----------------------------


def test_redteam_watermark_is_chronological_across_rfc3339_offset_forms(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """Two valid RFC3339 forms: '2026-09-01T23:30:00Z' vs '2026-09-01T23:00:00-05:00'
    (= 2026-09-02T04:00Z, chronologically LATER). Ingest canonicalizes each
    modified_time to UTC 'YYYY-MM-DDTHH:MM:SSZ', so the watermark reports the
    chronologically-latest ingested instant, not the lexicographically-largest string."""
    files = [
        {
            "file_id": "a",
            "path": "docs/a.md",
            "title": "A",
            "modified_time": "2026-09-01T23:30:00Z",
            "content": "## A\naword",
        },
        {
            "file_id": "b",
            "path": "docs/b.md",
            "title": "B",
            "modified_time": "2026-09-01T23:00:00-05:00",
            "content": "## B\nbword",
        },
    ]
    result = knowledge_sync({"files": files}, None, None, None, FakeClock(), knowledge)
    assert result["ok"] is True
    stored = conn.execute("SELECT modified_time FROM knowledge WHERE file_id = 'b'").fetchone()
    assert stored is not None
    assert stored["modified_time"] == "2026-09-02T04:00:00Z"
    assert result["data"]["watermark"] == "2026-09-02T04:00:00Z"


def test_redteam_whitespace_only_content_stores_title_path_row(
    conn: sqlite3.Connection, knowledge: KnowledgeRepo
) -> None:
    """B5 residual (T2.17 adoption review): whitespace-only text still leaves a trace.

    Same shape as B5: a file whose content is only whitespace must store the
    title/path-only row (searchable) and advance the watermark, not vanish.
    """
    files = [
        {
            "file_id": "ws",
            "path": "docs/ws.md",
            "title": "Whitespace doc",
            "modified_time": "2026-09-05T00:00:00Z",
            "content": " \n \t ",
        }
    ]
    result = knowledge_sync({"files": files}, None, None, None, FakeClock(), knowledge)

    assert result["ok"] is True
    row = conn.execute("SELECT body, title FROM knowledge WHERE file_id = 'ws'").fetchone()
    assert row is not None and row["body"] == "" and row["title"] == "Whitespace doc"
    hits = knowledge.search("whitespace", limit=5)
    assert len(hits) == 1 and hits[0].file_id == "ws"
    assert result["data"]["watermark"] == "2026-09-05T00:00:00Z"
