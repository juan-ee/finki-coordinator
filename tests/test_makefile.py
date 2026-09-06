"""Makefile contract (v7): no sync machinery; the site-build target is docker-pinned.

The v6.1 `sync` target wrapped scripts/sync_knowledge.py; both are deleted with the
machinery (proposal §12) — this guard keeps them from creeping back. T2.30 adds
`site-build`: the MkDocs Material build of the Pi-local record, docker-pinned.
"""

import pathlib
import re

MAKEFILE_PATH = pathlib.Path(__file__).resolve().parents[1] / "Makefile"


def test_makefile_has_no_sync_target() -> None:
    """The deleted sync target stays deleted: no 'sync:' recipe, no sync script reference."""
    text = MAKEFILE_PATH.read_text(encoding="utf-8")

    assert "\nsync:" not in text, "the v6.1 sync target must stay deleted (T2.28)"
    assert "sync_knowledge" not in text, "the deleted sync script must stay unreferenced"


def test_site_build_target_runs_mkdocs_material_in_docker() -> None:
    """site-build renders the record with the arm64 mkdocs-material image — no LLM.

    The recipe is pinned exactly: repo mounted at /workspace, the committed
    mkdocs.yml drives the build, output lands in data/site (T2.30)."""
    text = MAKEFILE_PATH.read_text(encoding="utf-8")

    match = re.search(r"(?ms)^site-build:\n\t([^\n]+)\n", text)
    assert match is not None, "no 'site-build:' target found in the Makefile"
    # The uid mapping MUST be $$(...) — a bare $(...) is a make variable and would
    # expand to empty before the shell ever sees it (review finding, T2.30).
    assert match.group(1) == (
        'docker run --rm --user "$$(id -u):$$(id -g)"'
        ' -v "$(CURDIR):/workspace" squidfunk/mkdocs-material build'
        " -f /workspace/mkdocs.yml -d /workspace/data/site"
    )


def test_site_build_target_is_phony() -> None:
    """site-build is declared .PHONY (a stale file named 'site-build' must not shadow it)."""
    text = MAKEFILE_PATH.read_text(encoding="utf-8")

    phony = re.search(r"(?m)^\.PHONY:(.*)$", text)
    assert phony is not None, "no .PHONY line found"
    assert "site-build" in phony.group(1).split()
