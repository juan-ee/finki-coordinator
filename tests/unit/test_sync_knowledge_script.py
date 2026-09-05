"""Script tests for scripts/sync_knowledge.py (T2.18): the thin I/O adapter.

The transport is a Protocol — these tests inject a FakeDrive that emulates the
google-workspace CLI's observed shapes (search returns {id, name, mimeType, modifiedTime}
metadata dicts; download writes content to a file). Ingestion goes through the real
KnowledgeRepo on a tmp_path DB; no network, no Docker, no real secrets.
"""

import importlib.util
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from coordinator.db import connect, migrate
from coordinator.repositories import KnowledgeRepo

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "sync_knowledge.py"


def _load_script() -> object:
    """Import scripts/sync_knowledge.py as a module (scripts sit outside the package)."""
    spec = importlib.util.spec_from_file_location("sync_knowledge_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_script = _load_script()


class FakeDrive:
    """Fake transport: a folder tree of CLI-shaped metadata dicts + file contents."""

    def __init__(
        self,
        tree: dict[str | None, list[dict[str, object]]],
        contents: dict[str, bytes],
        fail_downloads: frozenset[str] = frozenset(),
    ) -> None:
        self.tree = tree
        self.contents = contents
        self.fail = fail_downloads
        self.download_calls: list[str] = []
        self.workspace: Path | None = None  # the download_workspace() declaration
        self.seen_output_dirs: list[Path] = []

    def list_children(self, folder_id: str | None) -> list[dict[str, object]]:
        """Return the folder's children (folder_id None = the My Drive root)."""
        return self.tree.get(folder_id, [])

    def download_workspace(self) -> Path | None:
        """Declare where downloads must live (None = system temp is fine)."""
        return self.workspace

    def download(self, file_id: str, output_dir: Path) -> Path:
        """Write the file's bytes to output_dir/<file_id>; raise on injected failures."""
        if file_id in self.fail:
            # the real transport wraps CLI failures in SyncError — mirror that contract
            raise sync_script.SyncError("gapi drive download failed (exit 1): quota exceeded")
        self.download_calls.append(file_id)
        self.seen_output_dirs.append(output_dir)
        path = output_dir / file_id
        path.write_bytes(self.contents.get(file_id, b""))
        return path


def _entry(
    file_id: str,
    name: str,
    mime_type: str,
    modified_time: str,
) -> dict[str, object]:
    """One CLI-shaped metadata dict (the fields the real CLI returns)."""
    return {"id": file_id, "name": name, "mimeType": mime_type, "modifiedTime": modified_time}


_FOLDER = "application/vnd.google-apps.folder"


def _tree() -> tuple[dict[str | None, list[dict[str, object]]], dict[str, bytes]]:
    """The observed Pi-shaped corpus: one folder, three root files, one nested file."""
    tree: dict[str | None, list[dict[str, object]]] = {
        None: [
            _entry("dir-notes", "notes", _FOLDER, "2026-09-05T09:19:39.613Z"),
            _entry("f-brief", "Brief.md", "text/markdown", "2026-08-26T21:28:50.000Z"),
            _entry("f-logo", "Fink-Labs Logo.html", "text/html", "2026-08-29T23:47:32.000Z"),
            _entry("f-pdf", "Informe.pdf", "application/pdf", "2026-08-27T07:54:14.000Z"),
        ],
        "dir-notes": [
            _entry("f-q", "Preguntas_para_David.md", "text/markdown", "2026-08-27T11:34:34.000Z"),
        ],
    }
    contents: dict[str, bytes] = {
        "f-brief": b"## Mission\nalpha marker\n",
        "f-logo": b"<html>beta marker</html>",
        "f-q": b"## Preguntas\ngamma marker\n",
    }
    return tree, contents


def _rows(db_path: Path) -> list[tuple[object, ...]]:
    """Read the full knowledge cache (chunk_id, file_id, body) via a fresh connection."""
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute("SELECT chunk_id, file_id, body FROM knowledge"))
    finally:
        conn.close()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """A tmp DB path; parents pre-created (the script also creates them on real runs)."""
    path = tmp_path / "runtime" / "hermes-coord.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def repo(db_path: Path) -> Iterator[KnowledgeRepo]:
    """A repository view over the script-created store, for assertions."""
    conn = connect(db_path)
    migrate(conn, applied_at="2026-01-01T00:00:00+00:00")
    yield KnowledgeRepo(conn)
    conn.close()


def test_first_round_ingests_the_whole_tree(db_path: Path, repo: KnowledgeRepo) -> None:
    """Round 1 on an empty cache: text files chunk, non-text index title/path only."""
    tree, contents = _tree()

    outcome = sync_script.run_sync(
        db_path, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00"
    )

    assert outcome.failed == []
    assert outcome.ingested == 4  # f-brief, f-logo, f-q (text) + f-pdf (index-only)
    assert outcome.plan.projected_watermark == "2026-08-29T23:47:32Z"
    assert [h.file_id for h in repo.search("alpha marker", limit=5)] == ["f-brief"]
    nested = repo.search("gamma", limit=5)
    assert nested[0].path == "notes/Preguntas_para_David.md"
    assert [h.file_id for h in repo.search("Informe", limit=5)] == ["f-pdf"]


def test_second_round_with_no_changes_is_a_noop(db_path: Path, repo: KnowledgeRepo) -> None:
    """Idempotency contract: the second run downloads nothing and ingests 0 files."""
    tree, contents = _tree()
    transport = FakeDrive(tree, contents)
    sync_script.run_sync(db_path, transport, fetched_at="2026-09-06T00:00:00+00:00")
    before = _rows(db_path)
    transport.download_calls.clear()  # forget round 1's attempts: only round 2 counts

    second = sync_script.run_sync(db_path, transport, fetched_at="2026-09-06T01:00:00+00:00")

    assert second.ingested == 0
    assert second.plan.downloads == ()
    assert second.plan.unchanged_count == 4
    assert transport.download_calls == []  # not even a download attempt
    assert _rows(db_path) == before


def test_drive_side_edit_becomes_findable_next_round(db_path: Path, repo: KnowledgeRepo) -> None:
    """The incremental delta: one edited file re-ingests, the old text disappears."""
    tree, contents = _tree()
    sync_script.run_sync(db_path, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00")

    tree["dir-notes"][0] = _entry(
        "f-q", "Preguntas_para_David.md", "text/markdown", "2026-09-07T08:00:00.000Z"
    )
    contents = dict(contents)
    contents["f-q"] = b"## Preguntas\ndelta marker v2\n"
    second = sync_script.run_sync(
        db_path, FakeDrive(tree, contents), fetched_at="2026-09-07T09:00:00+00:00"
    )

    assert second.ingested == 1
    assert repo.search("delta marker v2", limit=5)[0].file_id == "f-q"
    assert repo.search("gamma marker", limit=5) == []
    assert repo.watermark() == "2026-09-07T08:00:00Z"


def test_resync_rebuilds_and_purges_files_gone_from_drive(
    db_path: Path, repo: KnowledgeRepo
) -> None:
    """--resync wipes the cache and rebuilds from the listing: a Drive-side deletion
    (the modifiedTime watermark's structural blind spot) is purged."""
    tree, contents = _tree()
    sync_script.run_sync(db_path, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00")

    tree[None] = [entry for entry in tree[None] if entry["id"] != "f-brief"]
    outcome = sync_script.run_sync(
        db_path, FakeDrive(tree, contents), resync=True, fetched_at="2026-09-06T02:00:00+00:00"
    )

    assert outcome.ingested == 3
    assert repo.search("alpha marker", limit=5) == []  # deleted from Drive -> purged
    assert repo.search("gamma", limit=5) != []
    assert repo.watermark() == "2026-08-29T23:47:32Z"


def test_dry_run_reports_the_plan_and_writes_nothing(db_path: Path, repo: KnowledgeRepo) -> None:
    """--dry-run lists what WOULD happen: no downloads, no rows, cache untouched."""
    tree, contents = _tree()
    transport = FakeDrive(tree, contents)

    outcome = sync_script.run_sync(
        db_path, transport, dry_run=True, fetched_at="2026-09-06T00:00:00+00:00"
    )

    assert outcome.ingested == 0
    assert outcome.dry_run is True
    assert len(outcome.plan.downloads) == 3
    assert len(outcome.plan.index_only) == 1
    assert transport.download_calls == []
    assert repo.watermark() is None  # nothing written


def test_download_failure_is_reported_and_the_rest_ingests(
    db_path: Path, repo: KnowledgeRepo
) -> None:
    """A failed download skips exactly that file (reason surfaced) and fails the run."""
    tree, contents = _tree()
    transport = FakeDrive(tree, contents, fail_downloads=frozenset({"f-brief"}))

    outcome = sync_script.run_sync(db_path, transport, fetched_at="2026-09-06T00:00:00+00:00")

    assert outcome.ingested == 3
    assert len(outcome.failed) == 1
    assert "f-brief" in outcome.failed[0]
    assert "quota" in outcome.failed[0]
    assert repo.search("alpha marker", limit=5) == []  # the failed file never stored
    assert repo.search("gamma", limit=5) != []  # the others did


def test_unparseable_modified_time_is_skipped_not_stored(
    db_path: Path, repo: KnowledgeRepo
) -> None:
    """Incremental: a file with an incomparable timestamp is skipped with a reason."""
    tree, contents = _tree()
    tree[None].append(_entry("f-bad", "weird.md", "text/markdown", "not-a-timestamp"))
    contents = dict(contents)
    contents["f-bad"] = b"## Weird\njunk marker\n"

    outcome = sync_script.run_sync(
        db_path, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00"
    )

    assert outcome.ingested == 4
    assert [s.file.file_id for s in outcome.plan.skipped] == ["f-bad"]
    assert repo.search("junk marker", limit=5) == []


def test_undecodable_text_download_falls_back_to_index_only(
    db_path: Path, repo: KnowledgeRepo
) -> None:
    """A text/* file whose bytes are not UTF-8 indexes title/path only (no crash)."""
    tree, contents = _tree()
    tree[None].append(_entry("f-bin", "blob.md", "text/markdown", "2026-09-01T00:00:00Z"))
    contents = dict(contents)
    contents["f-bin"] = b"\xff\xfe\x00\x01 not utf-8"

    outcome = sync_script.run_sync(
        db_path, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00"
    )

    assert outcome.ingested == 5
    assert outcome.failed == []
    assert [h.file_id for h in repo.search("blob", limit=5)] == ["f-bin"]
    rows = [row for row in _rows(db_path) if row[1] == "f-bin"]
    assert len(rows) == 1 and rows[0][2] == ""


def test_empty_text_file_gets_title_path_only_row(db_path: Path, repo: KnowledgeRepo) -> None:
    """An empty text file still leaves its trace: one empty-body, searchable-by-title row."""
    tree, contents = _tree()
    tree[None].append(_entry("f-empty", "Empty.md", "text/markdown", "2026-09-02T00:00:00Z"))
    contents = dict(contents)
    contents["f-empty"] = b""

    outcome = sync_script.run_sync(
        db_path, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00"
    )

    assert outcome.ingested == 5
    assert [h.file_id for h in repo.search("Empty", limit=5)] == ["f-empty"]
    assert repo.watermark() == "2026-09-02T00:00:00Z"  # the trace advances the watermark


def test_whitespace_only_text_file_stores_title_path_only_row(
    db_path: Path, repo: KnowledgeRepo
) -> None:
    """B5 residual (T2.17 adoption, migrated from the removed handler's tests):
    whitespace-only text must store the title/path-only row, not vanish."""
    tree, contents = _tree()
    tree[None].append(_entry("f-ws", "Whitespace doc.md", "text/markdown", "2026-09-05T00:00:00Z"))
    contents = dict(contents)
    contents["f-ws"] = b" \n \t "

    outcome = sync_script.run_sync(
        db_path, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00"
    )

    assert outcome.ingested == 5
    assert outcome.failed == []
    hits = repo.search("Whitespace", limit=5)
    assert len(hits) == 1 and hits[0].file_id == "f-ws"
    rows = [row for row in _rows(db_path) if row[1] == "f-ws"]
    assert len(rows) == 1 and rows[0][2] == ""
    assert repo.watermark() == "2026-09-05T00:00:00Z"  # the trace advances the watermark


def test_duplicate_file_id_across_folders_ingests_once(db_path: Path, repo: KnowledgeRepo) -> None:
    """A file reachable twice (Drive shortcut edge) ingests once — first BFS path wins."""
    tree, contents = _tree()
    tree["dir-notes"].append(
        _entry("f-brief", "Brief.md", "text/markdown", "2026-08-26T21:28:50.000Z")
    )

    outcome = sync_script.run_sync(
        db_path, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00"
    )

    assert outcome.ingested == 4
    brief_rows = [row for row in _rows(db_path) if row[1] == "f-brief"]
    assert all(row[2] != "" for row in brief_rows)  # chunks stored exactly once
    assert [h.path for h in repo.search("alpha marker", limit=5)] == ["Brief.md"]


def test_run_sync_creates_missing_db_parent_dirs(tmp_path: Path) -> None:
    """The script mkdir -p's the DB location (init_db.py parity) before connecting."""
    tree, contents = _tree()
    deep = tmp_path / "a" / "b" / "hermes-coord.db"

    outcome = sync_script.run_sync(
        deep, FakeDrive(tree, contents), fetched_at="2026-09-06T00:00:00+00:00"
    )

    assert outcome.ingested == 4


def test_parse_args_defaults_and_flags() -> None:
    """Flag surface: --resync/--dry-run/--db parse; dry-run defaults False."""
    args = sync_script.parse_args(["--resync", "--db", "/tmp/x.db"])
    assert args.resync is True and args.dry_run is False and str(args.db) == "/tmp/x.db"

    plain = sync_script.parse_args([])
    assert plain.resync is False and plain.dry_run is False


def test_downloads_happen_inside_the_transport_declared_workspace(
    db_path: Path, tmp_path: Path
) -> None:
    """Docker-transport contract: the run's download temp dir lives INSIDE the
    workspace the transport declares (the compose-mounted data dir) — a CLI running
    in the container and the host script must see the same files."""
    tree, contents = _tree()
    transport = FakeDrive(tree, contents)
    workspace = tmp_path / "declared-workspace"
    workspace.mkdir()
    transport.workspace = workspace

    outcome = sync_script.run_sync(db_path, transport, fetched_at="2026-09-06T00:00:00+00:00")

    assert outcome.ingested == 4
    assert transport.seen_output_dirs, "downloads must have happened"
    assert all(directory.is_relative_to(workspace) for directory in transport.seen_output_dirs)


def test_cli_runs_with_stdin_detached(monkeypatch, tmp_path: Path) -> None:
    """The CLI subprocess must never inherit stdin: docker compose exec -T otherwise
    consumes the calling shell's remaining input (bit the acceptance run and any
    piped batch of rounds)."""
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        captured.update(kwargs)
        return type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

    monkeypatch.setattr(sync_script.subprocess, "run", fake_run)
    transport = sync_script.GapiCliTransport(
        mode="direct", gapi_path="/nowhere/google_api.py", compose_dir=tmp_path
    )

    transport.list_children(None)

    assert captured["stdin"] == sync_script.subprocess.DEVNULL


def test_container_download_path_maps_data_under_host_to_the_mount() -> None:
    """A host download path under <repo>/data maps to /opt/data/workspace/...."""
    compose_dir = Path("/repo")

    mapped = sync_script.container_download_path(
        compose_dir, Path("/repo/data/sync-knowledge-abc/f-id")
    )

    assert mapped == Path("/opt/data/workspace/sync-knowledge-abc/f-id")


def test_container_download_path_resolves_symlinked_data_dirs(tmp_path: Path) -> None:
    """resolve() on both sides: a symlinked data dir still maps onto the mount."""
    real_data = tmp_path / "data"
    real_data.mkdir()
    linked = tmp_path / "data-link"
    os.symlink(real_data, linked)
    compose_dir = linked.parent  # compose_dir/data must be the REAL dir after resolve

    mapped = sync_script.container_download_path(compose_dir, linked / "tmp-xyz" / "f-id")

    assert mapped == Path("/opt/data/workspace/tmp-xyz/f-id")


def test_container_download_path_rejects_paths_outside_the_data_dir() -> None:
    """A download path the mount cannot deliver is a loud programming error."""
    with pytest.raises(sync_script.SyncError):
        sync_script.container_download_path(Path("/repo"), Path("/etc/nowhere/f-id"))
