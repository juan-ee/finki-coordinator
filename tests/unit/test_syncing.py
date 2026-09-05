"""Pure plan logic for the deterministic knowledge sync (v6.1, T2.18): watermark diff.

The plan is I/O-free: a listing of Drive file metadata plus the stored watermark goes
in, an ingest plan comes out. Drive-side timestamps use the real CLI forms observed on
the Pi (millisecond-precision RFC3339, e.g. '2026-09-05T09:19:39.613Z'); cached
watermarks are the canonical 'YYYY-MM-DDTHH:MM:SSZ' form the cache stores.
"""

from coordinator.syncing import (
    DriveFile,
    canonical_modified_time,
    is_text_mime,
    plan_sync,
)


def _file(
    file_id: str,
    path: str,
    modified_time: str,
    mime_type: str = "text/markdown",
) -> DriveFile:
    """Build one listed Drive file (title defaults to the path's basename)."""
    return DriveFile(
        file_id=file_id,
        path=path,
        title=path.rsplit("/", 1)[-1],
        modified_time=modified_time,
        mime_type=mime_type,
    )


# --- canonical_modified_time (moved here from handlers; the ingest path stores this form) ---


def test_canonical_passthrough_of_already_canonical_form() -> None:
    """A canonical value maps to itself."""
    assert canonical_modified_time("2026-09-05T09:25:35Z") == "2026-09-05T09:25:35Z"


def test_canonical_truncates_drive_millisecond_form_to_utc_seconds() -> None:
    """The Drive CLI's millisecond Z form canonicalizes to whole UTC seconds."""
    assert canonical_modified_time("2026-09-05T09:19:39.613Z") == "2026-09-05T09:19:39Z"


def test_canonical_converts_explicit_offset_to_utc() -> None:
    """An offset form is converted to UTC (chronological watermark, B6 rule)."""
    assert canonical_modified_time("2026-09-02T06:00:00+02:00") == "2026-09-02T04:00:00Z"


def test_canonical_returns_unparseable_value_unchanged() -> None:
    """Bad Drive metadata is stored unchanged — visible, never silently rewritten."""
    assert canonical_modified_time("not-a-timestamp") == "not-a-timestamp"


# --- is_text_mime ---------------------------------------------------------------------------


def test_text_mime_family_is_downloadable() -> None:
    """Every text/* type (markdown, html, plain, csv) is ingestable as text."""
    for mime in ("text/markdown", "text/html", "text/plain", "text/csv"):
        assert is_text_mime(mime), mime


def test_non_text_mimes_are_index_only() -> None:
    """Binaries and Google-native types index title/path only (agent extracts live)."""
    for mime in (
        "application/pdf",
        "image/png",
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.folder",
        "",
    ):
        assert not is_text_mime(mime), mime


# --- plan_sync: selection -------------------------------------------------------------------


def test_empty_cache_selects_everything() -> None:
    """Watermark None (empty cache) = full ingest: every text file downloads."""
    files = [
        _file("f1", "Brief.md", "2026-08-26T21:28:50.000Z"),
        _file("f2", "notes/x.md", "2026-08-27T11:34:34.000Z"),
    ]

    plan = plan_sync(files, None)

    assert [c.file.file_id for c in plan.downloads] == ["f1", "f2"]
    assert plan.index_only == ()
    assert plan.unchanged_count == 0
    assert plan.skipped == ()
    assert plan.projected_watermark == "2026-08-27T11:34:34Z"


def test_files_at_or_below_watermark_are_unchanged() -> None:
    """Strictly-greater selection: a second run with no Drive-side changes ingests 0."""
    files = [
        _file("f1", "Brief.md", "2026-09-05T09:25:35.000Z"),
        _file("f2", "older.md", "2026-09-01T00:00:00Z"),
    ]

    plan = plan_sync(files, "2026-09-05T09:25:35Z")

    assert plan.downloads == ()
    assert plan.unchanged_count == 2
    assert plan.projected_watermark == "2026-09-05T09:25:35Z"


def test_drive_millisecond_form_matches_seconds_watermark() -> None:
    """The CLI's .613Z form truncates to the same canonical second: no re-ingest."""
    files = [_file("f1", "Brief.md", "2026-09-05T09:25:35.613Z")]

    plan = plan_sync(files, "2026-09-05T09:25:35Z")

    assert plan.downloads == ()
    assert plan.unchanged_count == 1


def test_only_files_past_the_watermark_are_selected() -> None:
    """The delta: one Drive-side edit lands, everything else stays cached."""
    files = [
        _file("old1", "a.md", "2026-08-26T21:28:50.000Z"),
        _file("edited", "b.md", "2026-09-06T10:00:00.000Z"),
        _file("old2", "c.md", "2026-09-05T09:25:35.000Z"),
    ]

    plan = plan_sync(files, "2026-09-05T09:25:35Z")

    assert [c.file.file_id for c in plan.downloads] == ["edited"]
    assert plan.unchanged_count == 2
    assert plan.projected_watermark == "2026-09-06T10:00:00Z"


def test_comparison_is_chronological_not_lexicographic() -> None:
    """An offset form that sorts ABOVE the watermark lexicographically but happened
    BEFORE it chronologically stays unchanged (B6 rule at the plan layer)."""
    files = [_file("f1", "a.md", "2026-09-05T10:00:00+09:00")]  # == 2026-09-05T01:00:00Z

    plan = plan_sync(files, "2026-09-05T09:25:35Z")

    assert plan.downloads == ()
    assert plan.unchanged_count == 1


def test_offset_form_past_the_watermark_is_selected() -> None:
    """An offset-form timestamp that happened AFTER the watermark is selected."""
    files = [_file("f1", "a.md", "2026-09-06T12:00:00+09:00")]  # == 2026-09-06T03:00:00Z

    plan = plan_sync(files, "2026-09-05T09:25:35Z")

    assert [c.file.file_id for c in plan.downloads] == ["f1"]
    assert plan.projected_watermark == "2026-09-06T03:00:00Z"


# --- plan_sync: resync ----------------------------------------------------------------------


def test_resync_selects_everything_regardless_of_watermark() -> None:
    """--resync rebuilds: files at, below, and above the watermark all selected."""
    files = [
        _file("f1", "a.md", "2026-09-01T00:00:00Z"),
        _file("f2", "b.md", "2026-09-09T00:00:00Z"),
    ]

    plan = plan_sync(files, "2026-09-05T09:25:35Z", resync=True)

    assert [c.file.file_id for c in plan.downloads] == ["f1", "f2"]
    assert plan.unchanged_count == 0
    # a rebuild's watermark derives from the rebuilt listing alone, not the old cache
    assert plan.projected_watermark == "2026-09-09T00:00:00Z"


# --- plan_sync: non-text + malformed metadata ------------------------------------------------


def test_non_text_files_are_index_only_but_still_selected() -> None:
    """PDFs and Google-native exports get a title/path-only row and advance the
    watermark (every ingested FILE leaves a trace — audit second-pass rule)."""
    files = [
        _file("pdf", "Informe.pdf", "2026-09-06T00:00:00Z", mime_type="application/pdf"),
        _file(
            "gdoc",
            "Spec",
            "2026-09-07T00:00:00Z",
            mime_type="application/vnd.google-apps.document",
        ),
    ]

    plan = plan_sync(files, "2026-09-05T09:25:35Z")

    assert plan.downloads == ()
    assert [c.file.file_id for c in plan.index_only] == ["pdf", "gdoc"]
    assert plan.projected_watermark == "2026-09-07T00:00:00Z"


def test_unparseable_modified_time_is_skipped_incrementally() -> None:
    """A file whose timestamp cannot be ordered is skipped with the reason surfaced,
    never silently ingested (which would break the no-op second run)."""
    files = [
        _file("bad", "weird.md", "not-a-timestamp"),
        _file("good", "a.md", "2026-09-06T00:00:00Z"),
    ]

    plan = plan_sync(files, "2026-09-05T09:25:35Z")

    assert [c.file.file_id for c in plan.downloads] == ["good"]
    assert len(plan.skipped) == 1
    assert plan.skipped[0].file.file_id == "bad"
    assert "not-a-timestamp" in plan.skipped[0].reason


def test_resync_also_skips_unparseable_timestamps() -> None:
    """Even a rebuild skips an unparseable modifiedTime: storing its raw string would
    make it the derived watermark (BINARY MAX above every real '2026-…' value) and
    permanently break every later incremental round."""
    files = [_file("bad", "weird.md", "not-a-timestamp")]

    plan = plan_sync(files, None, resync=True)

    assert plan.downloads == ()
    assert [s.file.file_id for s in plan.skipped] == ["bad"]
    assert "not-a-timestamp" in plan.skipped[0].reason


def test_unparseable_stored_watermark_falls_back_to_full_selection() -> None:
    """A corrupt watermark cannot order anything: rebuild semantics (select all)
    converge the cache instead of silently skipping every file."""
    files = [_file("f1", "a.md", "2026-09-06T00:00:00Z")]

    plan = plan_sync(files, "corrupted-watermark")

    assert [c.file.file_id for c in plan.downloads] == ["f1"]


def test_plan_output_is_deterministically_ordered() -> None:
    """Selections sort by (path, file_id) so runs and reports are stable."""
    files = [
        _file("z", "b.md", "2026-09-06T00:00:00Z"),
        _file("a", "a.md", "2026-09-06T00:00:00Z"),
        _file("y", "a.md", "2026-09-06T01:00:00Z"),
    ]

    plan = plan_sync(files, None)

    assert [c.file.file_id for c in plan.downloads] == ["a", "y", "z"]
