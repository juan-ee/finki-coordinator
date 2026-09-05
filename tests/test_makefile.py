"""Makefile contract (T2.19): make sync wraps the deterministic sync script via uv run."""

import pathlib
import re

MAKEFILE_PATH = pathlib.Path(__file__).resolve().parents[1] / "Makefile"


def test_sync_target_runs_the_script_through_uv_run() -> None:
    """The sync target exists and its recipe is exactly the uv-wrapped script call."""
    text = MAKEFILE_PATH.read_text(encoding="utf-8")

    match = re.search(r"(?ms)^sync:\n\t([^\n]+)\n", text)
    assert match is not None, "no 'sync:' target found in the Makefile"
    assert match.group(1) == "uv run python scripts/sync_knowledge.py"


def test_sync_target_is_phony() -> None:
    """sync is declared .PHONY (a stale file named 'sync' must never shadow it)."""
    text = MAKEFILE_PATH.read_text(encoding="utf-8")

    phony = re.search(r"(?m)^\.PHONY:(.*)$", text)
    assert phony is not None, "no .PHONY line found"
    assert "sync" in phony.group(1).split()
