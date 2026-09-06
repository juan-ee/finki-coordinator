"""Makefile contract (v7, T2.28): no Drive→cache sync machinery remains.

The v6.1 `sync` target wrapped scripts/sync_knowledge.py; both are deleted with the
machinery (proposal §12) — this guard keeps them from creeping back.
"""

import pathlib

MAKEFILE_PATH = pathlib.Path(__file__).resolve().parents[1] / "Makefile"


def test_makefile_has_no_sync_target() -> None:
    """The deleted sync target stays deleted: no 'sync:' recipe, no sync script reference."""
    text = MAKEFILE_PATH.read_text(encoding="utf-8")

    assert "\nsync:" not in text, "the v6.1 sync target must stay deleted (T2.28)"
    assert "sync_knowledge" not in text, "the deleted sync script must stay unreferenced"
