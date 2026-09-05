"""Tests for the knowledge subsystem: markdown chunking (D2) and the SQLite cache.

The cache (knowledge/knowledge_fts in hermes-coord.db) is rebuildable from Drive —
the index, not the record. TDD anchor from the audit: MATCH 'decision' must find
a chunk containing "decisión" (unicode61 remove_diacritics 2).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from coordinator.db import connect, migrate
from coordinator.knowledge import chunk_markdown
from coordinator.repositories import KnowledgeRepo

FIXED_FETCHED_AT = "2026-09-04T10:00:00Z"


@pytest.fixture()
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a migrated tmp_path database connection, closed after the test."""
    c = connect(tmp_path / "knowledge.db")
    migrate(c, applied_at="2026-01-01T00:00:00+00:00")
    yield c
    c.close()


# --- chunker (pure core) ----------------------------------------------------------------


def test_chunker_splits_on_h2_sections() -> None:
    """One chunk per ## section; the ## heading line itself is not body text."""
    body = "## First\nfirst body\n## Second\nsecond body\n"

    chunks = chunk_markdown(body)

    assert [(c.heading, c.body) for c in chunks] == [
        ("First", "first body"),
        ("Second", "second body"),
    ]


def test_chunker_leading_preamble_is_a_null_heading_chunk() -> None:
    """Content before the first ## forms its own chunk with heading None."""
    body = "preamble text\n## Decisions\ndecision body\n"

    chunks = chunk_markdown(body)

    assert [c.heading for c in chunks] == [None, "Decisions"]
    assert chunks[0].body == "preamble text"


def test_chunker_headingless_document_is_one_chunk() -> None:
    """A document with no ## headings is exactly one chunk (heading None)."""
    chunks = chunk_markdown("just some text\nmore text\n")

    assert len(chunks) == 1
    assert chunks[0].heading is None
    assert chunks[0].body == "just some text\nmore text"


def test_chunker_h3_stays_inside_its_h2_chunk() -> None:
    """### subsections do not split: their content belongs to the enclosing ## chunk."""
    body = "## Section\nintro\n### Sub\nsub body\nstill sub\n"

    chunks = chunk_markdown(body)

    assert len(chunks) == 1
    assert chunks[0].heading == "Section"
    assert "### Sub" in chunks[0].body
    assert "still sub" in chunks[0].body


def test_chunker_duplicate_headings_get_occurrence_suffixes() -> None:
    """Repeated ## headings are disambiguated so UNIQUE(file_id, heading) can hold."""
    body = "## Notes\na\n## Notes\nb\n## Notes\nc\n"

    chunks = chunk_markdown(body)

    assert [c.heading for c in chunks] == ["Notes", "Notes (2)", "Notes (3)"]


def test_chunker_fenced_heading_lines_do_not_split() -> None:
    """A '## ' line inside a code fence is body text, never a section heading."""
    body = "## Real\nintro\n```\n## fake heading inside fence\n```\ntail"

    chunks = chunk_markdown(body)

    assert len(chunks) == 1
    assert chunks[0].heading == "Real"
    assert chunks[0].body == "intro\n```\n## fake heading inside fence\n```\ntail"


def test_chunker_unclosed_fence_suppresses_splits_to_eof() -> None:
    """An unterminated fence keeps suppressing '## ' splits until the document ends."""
    body = "## Real\nintro\n```\n## still inside\nno close"

    chunks = chunk_markdown(body)

    assert len(chunks) == 1
    assert chunks[0].heading == "Real"
    assert chunks[0].body == "intro\n```\n## still inside\nno close"


def test_chunker_splits_again_after_a_closed_tilde_fence() -> None:
    """~~~ fences (info string allowed) toggle too; past the close, headings split."""
    body = "## Real\n~~~ruby\n## fake\n~~~\n## After\nafter body"

    chunks = chunk_markdown(body)

    assert [c.heading for c in chunks] == ["Real", "After"]
    assert chunks[0].body == "~~~ruby\n## fake\n~~~"
    assert chunks[1].body == "after body"


def test_chunker_blank_body_yields_no_chunks() -> None:
    """An empty/whitespace-only document produces no chunks (nothing to index)."""
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []


def test_chunker_strips_section_body_edges() -> None:
    """Leading/trailing blank lines inside a section are stripped from the chunk body."""
    body = "## A\n\n\nreal content\n\n\n"

    chunks = chunk_markdown(body)

    assert chunks[0].body == "real content"


# --- repository (SQLite cache + FTS5) ---------------------------------------------------


def _replace_drive_doc(
    repo: KnowledgeRepo,
    *,
    file_id: str = "f1",
    path: str = "docs/decisions/0001.md",
    title: str = "0001 Use FTS5",
    modified_time: str = "2026-09-01T00:00:00Z",
    body: str,
) -> int:
    """Replace one Drive document with chunks of the given body; return the row count."""
    chunks = chunk_markdown(body)
    return repo.replace_file(
        file_id=file_id,
        path=path,
        title=title,
        modified_time=modified_time,
        fetched_at=FIXED_FETCHED_AT,
        chunks=chunks,
    )


def test_replace_file_stores_chunks_and_finds_them(conn: sqlite3.Connection) -> None:
    """replace_file indexes every chunk; search finds a body match with metadata."""
    repo = KnowledgeRepo(conn)

    stored = _replace_drive_doc(
        repo,
        body="## Decision\nwe adopted sqlite fts5 for the knowledge cache\n",
    )

    assert stored == 1
    hits = repo.search("fts5", limit=3)
    assert len(hits) == 1
    assert hits[0].file_id == "f1"
    assert hits[0].path == "docs/decisions/0001.md"
    assert hits[0].title == "0001 Use FTS5"
    assert hits[0].heading == "Decision"


def test_replace_file_twice_reindexes_idempotently(conn: sqlite3.Connection) -> None:
    """Per-file reindex = DELETE + reINSERT: re-syncing replaces content, never dupes."""
    repo = KnowledgeRepo(conn)
    _replace_drive_doc(repo, body="## Old\nold text alpha\n")

    _replace_drive_doc(repo, body="## New\nnew text beta\n")

    hits = repo.search("alpha", limit=10)
    assert hits == []  # old content gone from the index
    hits = repo.search("beta", limit=10)
    assert len(hits) == 1
    rows = list(conn.execute("SELECT count(*) AS n FROM knowledge WHERE file_id = 'f1'"))
    assert rows[0]["n"] == 1


def test_search_matches_diacritics_insensitively(conn: sqlite3.Connection) -> None:
    """The audit's TDD anchor: MATCH 'decision' finds a chunk containing "decisión"."""
    repo = KnowledgeRepo(conn)
    _replace_drive_doc(
        repo,
        body="## Registro\ntomamos la decisión de migrar la base de conocimiento\n",
    )

    hits = repo.search("decision", limit=5)

    assert len(hits) == 1
    assert (
        "decisión"
        in conn.execute(
            "SELECT body FROM knowledge WHERE chunk_id = ?", (hits[0].chunk_id,)
        ).fetchone()["body"]
    )


def test_title_match_ranks_above_body_match(conn: sqlite3.Connection) -> None:
    """bm25 weights title 10:1 over body: the title hit comes first."""
    repo = KnowledgeRepo(conn)
    _replace_drive_doc(
        repo,
        file_id="title-hit",
        path="docs/title.md",
        title="Export pipeline",
        body="## Notes\nmentions the word export once\n",
    )
    _replace_drive_doc(
        repo,
        file_id="body-hit",
        path="docs/body.md",
        title="Unrelated title",
        body="## Notes\nexport export export export\n",
    )

    hits = repo.search("export", limit=10)

    assert [h.file_id for h in hits] == ["title-hit", "body-hit"]


def test_integrity_check_passes_on_consistent_index(conn: sqlite3.Connection) -> None:
    """The phase-gate command (rank form) runs clean when the sync owns all writes.

    Rank form (-1) is required: the plain 'integrity-check' detects nothing on
    SQLite 3.50.4 or 3.51.3; the rank form detects both desync directions on the
    suite's SQLite 3.50.4 (pinned by the out-of-band delete/insert tests below).
    """
    repo = KnowledgeRepo(conn)
    _replace_drive_doc(repo, body="## A\ncontent alpha\n")

    conn.execute("INSERT INTO knowledge_fts(knowledge_fts, rank) VALUES('integrity-check', -1)")


def test_integrity_check_raises_after_out_of_band_delete(conn: sqlite3.Connection) -> None:
    """A raw knowledge-row DELETE leaves orphaned index entries: caught loudly.

    The T2.13-specced direction: the repo populates the index, a knowledge row is
    deleted out-of-band, and the rank-form integrity-check must raise.
    """
    repo = KnowledgeRepo(conn)
    _replace_drive_doc(repo, body="## A\ncontent alpha\n")
    conn.execute("DELETE FROM knowledge WHERE file_id = 'f1'")  # bypasses the FTS side

    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("INSERT INTO knowledge_fts(knowledge_fts, rank) VALUES('integrity-check', -1)")


def test_integrity_check_raises_after_out_of_band_insert(conn: sqlite3.Connection) -> None:
    """A content row inserted without the FTS side desyncs the index: caught loudly.

    (Direction note, verified on the suite's SQLite 3.50.4: the rank form detects
    BOTH desync directions — a content row missing from the index (this test) and
    the orphaned index entries after a raw content DELETE (the delete test above).
    The plain form detects nothing on 3.50.4 or 3.51.3; the repo owning all writes
    is what prevents both desyncs in the first place.)
    """
    repo = KnowledgeRepo(conn)
    _replace_drive_doc(repo, body="## A\ncontent alpha\n")
    conn.execute(
        "INSERT INTO knowledge (file_id, path, title, heading, body, modified_time,"
        " fetched_at) VALUES ('zz', 'p', 't', NULL, 'b', '2026', '2026')"
    )  # bypasses the FTS side

    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("INSERT INTO knowledge_fts(knowledge_fts, rank) VALUES('integrity-check', -1)")


def test_watermark_is_max_modified_time_or_none(conn: sqlite3.Connection) -> None:
    """Derived watermark: MAX(modified_time) over cached rows; None when the cache is empty."""
    repo = KnowledgeRepo(conn)
    assert repo.watermark() is None

    _replace_drive_doc(repo, file_id="a", modified_time="2026-09-01T00:00:00Z", body="## A\na\n")
    _replace_drive_doc(repo, file_id="b", modified_time="2026-09-03T00:00:00Z", body="## B\nb\n")

    assert repo.watermark() == "2026-09-03T00:00:00Z"


def test_replace_file_delete_older_than_watermark_stays_searchable(
    conn: sqlite3.Connection,
) -> None:
    """Incremental sync contract: replacing one file never disturbs other files' rows."""
    repo = KnowledgeRepo(conn)
    _replace_drive_doc(repo, file_id="keep", body="## Keep\nkeepme\n")

    _replace_drive_doc(repo, file_id="f1", body="## Mine\nmineme\n")

    assert len(repo.search("keepme", limit=5)) == 1
    assert len(repo.search("mineme", limit=5)) == 1
