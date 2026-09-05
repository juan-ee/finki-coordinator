#!/usr/bin/env python3
"""Deterministic Drive -> knowledge-cache sync (v6.1, D2 rev; AGENTS.md hard rule 11).

The script is the ONLY writer of the knowledge cache. Each round: list the knowledge
Drive via the google-workspace skill CLI (per-folder children queries — the CLI's
search output has no parents field), ask coordinator.syncing.plan_sync what to ingest
(pure plan: watermark diff + selection), download changed TEXT files into a temp dir,
and ingest through the plugin's own chunker + KnowledgeRepository (per-file reindex =
DELETE + reINSERT). File content lives only inside this process and the temp dir — it
is never printed, never logged, never relayed to an LLM; the report carries counts,
paths, and reasons only. The watermark stays derived (MAX(modified_time) over cached
rows) — there is no knob to clobber.

Run targets (proposal §3):
- dev host:      make sync  (uv run python scripts/sync_knowledge.py)
- Pi host:       python3 scripts/sync_knowledge.py --transport docker
                 (the CLI is reached via docker compose exec -T gateway ...)
- in container:  python3 <HERMES_HOME>/scripts/sync_knowledge.py --transport direct
                 (the nightly hermes cron job; scripts/setup.sh installs the script)

Flags: --resync wipes the cache and rebuilds from the full listing (the way to purge
Drive-side deletions — the modifiedTime watermark's structural blind spot);
--dry-run lists + plans only (no downloads, no writes).
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


def _ensure_coordinator_importable() -> None:
    """Make 'import coordinator' work: installed package, repo src/, or plugin dir."""
    try:
        import coordinator  # noqa: F401

        return
    except ImportError:
        pass
    for base in (
        Path(__file__).resolve().parents[1] / "src",  # a repo checkout (dev host, Pi host)
        Path("/opt/data/plugins"),  # the container's plugin parent (compose mount)
    ):
        if (base / "coordinator" / "__init__.py").exists():
            sys.path.insert(0, str(base))
            return
    raise SystemExit(
        "sync_knowledge: cannot import the coordinator package — run from a repo"
        " checkout, or ensure the plugin is mounted at /opt/data/plugins/coordinator"
    )


_ensure_coordinator_importable()

from coordinator import syncing
from coordinator.db import connect, migrate
from coordinator.knowledge import Chunk, chunk_markdown
from coordinator.repositories import KnowledgeRepo

GAPI_DEFAULT_PATH = "/opt/data/skills/productivity/google-workspace/scripts/google_api.py"
"""The skill CLI's in-container path (HERMES_HOME=/opt/data, compose mounts)."""

_GATEWAY_SERVICE = "gateway"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_LIST_PAGE_MAX = 200  # per-listing page cap; a full page prints a truncation warning
_BFS_MAX_DEPTH = 10


class SyncError(Exception):
    """Raised when the Drive transport fails (non-zero CLI exit, unparsable output)."""


class DriveTransport(Protocol):
    """The sync's only Drive seam (rule 3): metadata listing + content download."""

    def list_children(self, folder_id: str | None) -> list[dict[str, object]]:
        """Return one folder's children as CLI-shaped metadata dicts (None = root)."""
        ...

    def download(self, file_id: str, output_dir: Path) -> Path:
        """Download the file's bytes into output_dir; return the written path."""
        ...


@dataclass
class SyncOutcome:
    """One finished round: the plan plus what actually happened (content never here)."""

    plan: syncing.SyncPlan
    ingested: int
    failed: list[str] = field(default_factory=list)
    dry_run: bool = False
    resync: bool = False


class GapiCliTransport:
    """Concrete transport: the google-workspace CLI, direct or via docker compose exec.

    'direct' runs python3 <gapi_path> ... (in-container: the CLI lives on the same
    filesystem); 'docker' runs docker compose exec -T gateway python3 <gapi_path> ...
    from the repo root (Pi host: the CLI exists only inside the container). Search
    stdout is parsed as JSON; download writes to a caller-supplied file — content
    never rides through this adapter's logs.
    """

    def __init__(self, *, mode: str, gapi_path: str, compose_dir: Path) -> None:
        self._mode = mode
        self._gapi_path = gapi_path
        self._compose_dir = compose_dir

    def _run(self, args: list[str]) -> str:
        """Run one CLI invocation; return stdout (raise SyncError on failure)."""
        if self._mode == "docker":
            argv = [
                "docker",
                "compose",
                "exec",
                "-T",
                _GATEWAY_SERVICE,
                "python3",
                self._gapi_path,
                *args,
            ]
            cwd: Path | None = self._compose_dir
        else:
            argv = ["python3", self._gapi_path, *args]
            cwd = None
        result = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            tail = detail[-1] if detail else "no output"
            raise SyncError(
                f"gapi {' '.join(args[:2])} failed (exit {result.returncode}): {tail[:200]}"
            )
        return result.stdout

    def list_children(self, folder_id: str | None) -> list[dict[str, object]]:
        """List one folder's non-trashed children via a raw Drive query."""
        parent = "root" if folder_id is None else folder_id
        query = f"'{parent}' in parents and trashed = false"
        stdout = self._run(["drive", "search", "--max", str(_LIST_PAGE_MAX), "--raw-query", query])
        try:
            rows = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SyncError(f"drive search returned non-JSON output: {exc}") from exc
        if not isinstance(rows, list):
            raise SyncError("drive search output is not a JSON list")
        if len(rows) >= _LIST_PAGE_MAX:
            print(
                f"warning: listing hit the {_LIST_PAGE_MAX}-item page cap —"
                " results may be truncated",
                file=sys.stderr,
            )
        return rows

    def download(self, file_id: str, output_dir: Path) -> Path:
        """Download one file into output_dir/<file_id> (id as name: no path surprises)."""
        dest = output_dir / file_id
        self._run(["drive", "download", file_id, "--output", str(dest)])
        return dest


def _repo_root() -> Path:
    """Return the repo root (the script's grandparent) — the docker compose project dir."""
    return Path(__file__).resolve().parents[1]


def _build_transport(args: argparse.Namespace) -> DriveTransport:
    """Resolve --transport/auto into a concrete CLI transport."""
    mode = args.transport
    if mode == "auto":
        mode = "direct" if Path(args.gapi_path).exists() else "docker"
    if mode == "docker" and shutil.which("docker") is None:
        raise SyncError(
            "transport 'docker' needs the docker CLI on PATH (or run inside the"
            " container with --transport direct)"
        )
    return GapiCliTransport(mode=mode, gapi_path=args.gapi_path, compose_dir=_repo_root())


def list_drive_files(transport: DriveTransport) -> list[syncing.DriveFile]:
    """Walk the My Drive tree breadth-first and return files with logical paths.

    The CLI's search output carries no parents field, so the tree is walked with one
    children query per folder ('<id>' in parents); a file's path is its parent chain
    joined by '/' (root files have no prefix). Folders never become files; a file
    reachable twice (Drive shortcut edge) is taken once — first BFS path wins.
    """
    files: list[syncing.DriveFile] = []
    seen_ids: set[str] = set()
    queue: list[tuple[str | None, str, int]] = [(None, "", 0)]
    while queue:
        folder_id, prefix, depth = queue.pop(0)
        for row in transport.list_children(folder_id):
            file_id = str(row.get("id", ""))
            name = str(row.get("name", ""))
            mime = str(row.get("mimeType", ""))
            if not file_id or not name or file_id in seen_ids:
                continue
            seen_ids.add(file_id)
            if mime == _FOLDER_MIME:
                if depth < _BFS_MAX_DEPTH:
                    queue.append((file_id, prefix + name + "/", depth + 1))
                continue
            files.append(
                syncing.DriveFile(
                    file_id=file_id,
                    path=prefix + name,
                    title=name,
                    modified_time=str(row.get("modifiedTime", "")),
                    mime_type=mime,
                )
            )
    return files


def _chunks_for(
    transport: DriveTransport, candidate: syncing.IngestCandidate, output_dir: Path
) -> list[Chunk]:
    """Produce one candidate file's chunks: download + decode text, or a title-only row."""
    if not syncing.is_text_mime(candidate.file.mime_type):
        return [Chunk(heading=None, body="")]  # non-text: index title/path only
    dest = transport.download(candidate.file.file_id, output_dir)
    raw = Path(dest).read_bytes()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [Chunk(heading=None, body="")]  # a text/* mime that lied: title/path only
    if content.strip() == "":
        return [Chunk(heading=None, body="")]  # empty text: still leaves its trace
    return chunk_markdown(content)


def _wipe_cache(conn: sqlite3.Connection) -> None:
    """Delete every cached chunk + FTS row (the --resync rebuild's first step)."""
    conn.execute("DELETE FROM knowledge_fts WHERE rowid IN (SELECT chunk_id FROM knowledge)")
    conn.execute("DELETE FROM knowledge")
    conn.commit()


def run_sync(
    db_path: str | Path,
    transport: DriveTransport,
    *,
    resync: bool = False,
    dry_run: bool = False,
    fetched_at: str,
) -> SyncOutcome:
    """Run one sync round against the given store and transport; return the outcome.

    Pure-plan decisions come from coordinator.syncing; this adapter owns every piece
    of I/O: connect + migrate, the Drive listings, temp-dir downloads, and the
    repository writes (per-file reindex via KnowledgeRepo.replace_file — the same
    code path the former tool used). Dry-run stops after the plan: no downloads, no
    writes (a resync's wipe included).
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        migrate(conn, applied_at=fetched_at)
        repo = KnowledgeRepo(conn)
        watermark = repo.watermark()
        listed = list_drive_files(transport)
        plan = syncing.plan_sync(listed, watermark, resync=resync)
        if dry_run:
            return SyncOutcome(plan=plan, ingested=0, dry_run=True, resync=resync)
        if resync:
            _wipe_cache(conn)
        failed: list[str] = []
        ingested = 0
        with tempfile.TemporaryDirectory(prefix="sync-knowledge-") as tmp:
            output_dir = Path(tmp)
            for candidate in (*plan.downloads, *plan.index_only):
                try:
                    chunks = _chunks_for(transport, candidate, output_dir)
                except (SyncError, OSError) as exc:
                    # transport/download failures skip exactly this file (reason
                    # surfaced, run fails); a repository failure aborts the round.
                    failed.append(f"{candidate.file.path} ({candidate.file.file_id}): {exc}")
                    continue
                repo.replace_file(
                    file_id=candidate.file.file_id,
                    path=candidate.file.path,
                    title=candidate.file.title,
                    modified_time=candidate.canonical_time,
                    fetched_at=fetched_at,
                    chunks=chunks,
                )
                ingested += 1
        return SyncOutcome(plan=plan, ingested=ingested, failed=failed, resync=resync)
    finally:
        conn.close()


def _default_db_path() -> Path:
    """HERMES_HOME layout in the container; <repo>/data/hermes/... on a host checkout."""
    home = os.environ.get("HERMES_HOME")
    if home:
        return Path(home) / "workspace" / "hermes" / "hermes-coord.db"
    return _repo_root() / "data" / "hermes" / "hermes-coord.db"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the flag surface (--db/--resync/--dry-run/--transport/--gapi-path)."""
    parser = argparse.ArgumentParser(
        prog="sync_knowledge",
        description="Deterministic Drive -> knowledge-cache sync (no LLM in the data path).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="coordinator DB path (default: $HERMES_HOME layout, else <repo>/data/hermes/)",
    )
    parser.add_argument(
        "--resync",
        action="store_true",
        help="wipe the cache and rebuild from the full Drive listing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list + plan only: no downloads, no writes",
    )
    parser.add_argument(
        "--transport",
        choices=("auto", "direct", "docker"),
        default="auto",
        help="how to reach the google-workspace CLI (default: auto — direct when the"
        " CLI path exists locally, docker compose exec otherwise)",
    )
    parser.add_argument(
        "--gapi-path",
        default=GAPI_DEFAULT_PATH,
        help=f"in-container path of the skill CLI (default: {GAPI_DEFAULT_PATH})",
    )
    args = parser.parse_args(argv)
    if args.db is None:
        args.db = _default_db_path()
    return args


def _report(outcome: SyncOutcome) -> None:
    """Print the round's report — counts, paths, reasons; never file content (rule 11)."""
    plan = outcome.plan
    mode = (
        "dry-run plan"
        if outcome.dry_run
        else ("resync rebuild" if outcome.resync else "incremental")
    )
    print(
        f"knowledge sync [{mode}]: {len(plan.downloads)} text +"
        f" {len(plan.index_only)} index-only selected, {plan.unchanged_count} unchanged,"
        f" {len(plan.skipped)} skipped"
    )
    for candidate in plan.downloads:
        print(f"  download: {candidate.file.path}")
    for candidate in plan.index_only:
        print(f"  index-only: {candidate.file.path}")
    for entry in plan.skipped:
        print(f"  skipped: {entry.file.path} ({entry.file.file_id}): {entry.reason}")
    if not outcome.dry_run:
        print(
            f"ingested {outcome.ingested} file(s); failed {len(outcome.failed)};"
            f" watermark {plan.projected_watermark or 'empty'}"
        )
    for line in outcome.failed:
        print(f"  FAILED: {line}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: build the transport, run one round, print the report, exit 0/1."""
    args = parse_args(argv)
    try:
        transport = _build_transport(args)
        outcome = run_sync(
            args.db,
            transport,
            resync=args.resync,
            dry_run=args.dry_run,
            fetched_at=datetime.now(UTC).isoformat(),
        )
    except SyncError as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    _report(outcome)
    return 1 if outcome.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
