"""Knowledge-sync plan logic: watermark diff + file selection (pure core, no I/O).

v6.1 (D2 rev): the deterministic script (scripts/sync_knowledge.py) lists the knowledge
Drive via the google-workspace CLI, asks THIS module what to do (plan_sync), then
downloads changed text files and ingests them through the plugin's own chunker +
KnowledgeRepository — no file content ever crosses LLM context (AGENTS.md hard rule 11).
Nothing here touches the network, the filesystem, SQLite, or a clock (AGENTS.md rule 3).
"""

from dataclasses import dataclass
from datetime import UTC, datetime


def _parse_rfc3339(value: str) -> datetime | None:
    """Parse an RFC3339 timestamp to an aware datetime (naive reads as UTC; None if bad)."""
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def canonical_modified_time(value: str) -> str:
    """Normalize an RFC3339 modified_time to canonical UTC 'YYYY-MM-DDTHH:MM:SSZ'.

    A trailing Z is read as +00:00, an explicit offset is converted to UTC, and a
    timestamp without an offset is treated as UTC (the cache is UTC-anchored, and
    naive astimezone() would smuggle in the host's locale). An unparseable value is
    returned unchanged - the caller decides what that means (the sync script SKIPS
    such files with a surfaced reason, so nothing stores a timestamp the derived
    watermark could not order). Canonical values are
    fixed-width UTC strings, so string ORDER over them is chronological order - the
    invariant that makes both the watermark's SQL MAX and this module's comparisons
    honest across the mixed RFC3339 forms Drive reports.
    """
    parsed = _parse_rfc3339(value)
    if parsed is None:
        return value
    return parsed.astimezone(UTC).isoformat(timespec="seconds")[:-6] + "Z"


@dataclass(frozen=True)
class DriveFile:
    """One Drive file as listed: metadata only (content never enters the plan)."""

    file_id: str
    path: str  # logical path within the Drive root (built by the listing adapter)
    title: str
    modified_time: str  # raw Drive form, e.g. '2026-09-05T09:19:39.613Z'
    mime_type: str


@dataclass(frozen=True)
class IngestCandidate:
    """One selected file plus the canonical timestamp its cache rows will store."""

    file: DriveFile
    canonical_time: str


@dataclass(frozen=True)
class SkippedFile:
    """One file deliberately not ingested, with the reason surfaced for the report."""

    file: DriveFile
    reason: str


@dataclass(frozen=True)
class SyncPlan:
    """What one sync round will do: selected downloads/index-only rows + bookkeeping.

    'index-only' files (non-text: PDFs, binaries, Google-native exports) get a
    title/path-only cache row — every ingested FILE leaves a trace and advances the
    watermark; only non-empty text is chunked. The projected watermark is the
    canonical maximum the cache will report after the round (the old watermark still
    counts for incremental rounds: unchanged files stay stored).
    """

    downloads: tuple[IngestCandidate, ...]
    index_only: tuple[IngestCandidate, ...]
    unchanged_count: int
    skipped: tuple[SkippedFile, ...]
    projected_watermark: str | None


_TEXT_MIME_PREFIX = "text/"


def is_text_mime(mime_type: str) -> bool:
    """True when the MIME type is plain text the script can chunk; anything else
    (PDFs, images, Google-native types, unknown) indexes title/path only."""
    return mime_type.startswith(_TEXT_MIME_PREFIX)


def _selection_key(candidate: IngestCandidate) -> tuple[str, str]:
    """Sort selected files by (path, file_id) so runs and reports are deterministic."""
    return (candidate.file.path, candidate.file.file_id)


def plan_sync(files: list[DriveFile], watermark: str | None, *, resync: bool = False) -> SyncPlan:
    """Select the files to ingest: strictly past the watermark — or everything on resync.

    The watermark is the cache's derived MAX(modified_time) in canonical form; a file
    is selected when resync is set, or the cache is empty (watermark None), or the
    file's canonical modifiedTime sorts strictly after it — so a second run with no
    Drive-side changes selects nothing (idempotency). Comparison happens between
    canonical fixed-width UTC strings (chronologically honest — see
    canonical_modified_time). A file whose modifiedTime cannot be parsed is SKIPPED
    with the reason surfaced, in EVERY mode — resync included: storing its raw
    string would put an unorderable timestamp in the cache, and under SQLite's
    BINARY MAX it would become the derived watermark, sorting above every real
    '2026-…' value — so every later incremental round would fall back to full
    selection and the no-op second run would be lost permanently. A stored
    watermark that cannot be parsed cannot order anything: full selection (rebuild
    semantics) converges the cache. Folders never reach this function — the listing
    adapter filters them out. Selections and skips are sorted by (path, file_id)
    for deterministic reports.
    """
    canonical_wm: str | None = None
    if watermark is not None and _parse_rfc3339(watermark) is not None:
        canonical_wm = canonical_modified_time(watermark)

    selected: list[IngestCandidate] = []
    skipped: list[SkippedFile] = []
    unchanged = 0
    selected_times: list[str] = []
    for file in files:
        parsed = _parse_rfc3339(file.modified_time)
        if parsed is None:
            skipped.append(
                SkippedFile(
                    file=file,
                    reason=(
                        f"unparseable modifiedTime {file.modified_time!r} —"
                        " cannot order against the watermark"
                    ),
                )
            )
            continue
        canonical_time = canonical_modified_time(file.modified_time)
        if not resync and canonical_wm is not None and canonical_time <= canonical_wm:
            unchanged += 1
            continue
        selected.append(IngestCandidate(file=file, canonical_time=canonical_time))
        selected_times.append(canonical_time)

    downloads = tuple(candidate for candidate in selected if is_text_mime(candidate.file.mime_type))
    index_only = tuple(
        candidate for candidate in selected if not is_text_mime(candidate.file.mime_type)
    )
    downloads = tuple(sorted(downloads, key=_selection_key))
    index_only = tuple(sorted(index_only, key=_selection_key))
    ordered_skipped = tuple(
        sorted(skipped, key=lambda entry: (entry.file.path, entry.file.file_id))
    )

    projected_pool = list(selected_times)
    if not resync and canonical_wm is not None:
        projected_pool.append(canonical_wm)
    projected = max(projected_pool) if projected_pool else None

    return SyncPlan(
        downloads=downloads,
        index_only=index_only,
        unchanged_count=unchanged,
        skipped=ordered_skipped,
        projected_watermark=projected,
    )
