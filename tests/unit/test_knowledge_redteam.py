"""Phase-gate red-team adoptions (T2.17): the knowledge-subsystem attack regressions.

Adopted from the phase-gate red team's adversarial battery (AGENTS.md review
protocol step 4, run at HEAD ddfbe54 ahead of the T2.17 manual gate; battery:
/tmp/redteam_knowledge.py, 13 failed / 31 passed). Every test here encodes a real
bug the red team broke open - fixed in the same change as this module - and pins
the FIXED behavior as a regression test; the red team's attack names keep a
redteam_ prefix. Bug map: B1 replace_file rolls back on ANY failure, not only
sqlite3.Error (tests 1-2, incl. no-poisoning-of-later-writes); B2 knowledge_search
rejects NUL/C0 control-character queries up front (test 3); B3 a fence closes only
on the same character and an at-least-as-long run (tests 4-5); B4 only CRLF/CR/LF
are markdown line endings (tests 6-10). The former B5 (empty text file leaves a
trace), B6 (chronological watermark) and undecodable-content tests pinned the
v6 agent-mediated knowledge_sync handler; that handler was removed in v6.1 (T2.20)
and their invariants now live in the deterministic sync's tests
(tests/unit/test_syncing.py + tests/unit/test_sync_knowledge_script.py).
Deterministic: tmp_path, fixed clock, no network.
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
