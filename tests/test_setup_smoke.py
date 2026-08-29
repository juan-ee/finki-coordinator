"""Smoke tests for scripts/setup.sh: bash syntax and no-write --dry-run behavior."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_SH = REPO_ROOT / "scripts" / "setup.sh"

# Dummy fixture values — deliberately fake; the script must never echo them back.
REQUIRED_ENV: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN": "test-telegram-token",
    "OPENROUTER_API_KEY": "test-openrouter-key",
    "GOOGLE_DRIVE_CLIENT_ID": "test-client-id",
    "GOOGLE_DRIVE_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_DRIVE_REFRESH_TOKEN": "test-refresh-token",
}
OPTIONAL_ENV: dict[str, str] = {
    "RCLONE_REMOTE": "shareddrive:",
    "RCLONE_ROOT_FOLDER_ID": "test-root-folder-id",
}
# RCLONE_REMOTE is a remote NAME (not a credential) and may appear in planned output.
SECRET_VALUES = (*REQUIRED_ENV.values(), OPTIONAL_ENV["RCLONE_ROOT_FOLDER_ID"])


def _snapshot(*roots: Path) -> list[tuple[str, int]]:
    """Sorted recursive (path, size) listing of the given roots (dirs marked size -1)."""
    entries: list[tuple[str, int]] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            rel = f"{root.name}/{path.relative_to(root)}"
            entries.append((rel, path.stat().st_size if path.is_file() else -1))
    return sorted(entries)


def test_setup_sh_passes_bash_syntax_check() -> None:
    """scripts/setup.sh exists and parses cleanly under the bash -n syntax check."""
    assert SETUP_SH.exists(), f"missing {SETUP_SH}"

    proc = subprocess.run(
        ["bash", "-n", str(SETUP_SH)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, f"bash -n failed:\n{proc.stderr}"


def test_dry_run_exits_zero_writes_nothing_and_leaks_no_secrets(tmp_path: Path) -> None:
    """--dry-run with fixture env exits 0, prints the plan, and touches nothing."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes-home"
    home.mkdir()
    hermes_home.mkdir()

    # Explicit env (no os.environ inheritance) keeps the run deterministic.
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "HERMES_HOME": str(hermes_home),
        **REQUIRED_ENV,
        **OPTIONAL_ENV,
    }

    before = _snapshot(tmp_path)

    proc = subprocess.run(
        ["bash", str(SETUP_SH), "--dry-run"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, f"dry-run failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"

    after = _snapshot(tmp_path)
    assert before == after, f"dry-run wrote under tmp roots: {sorted(set(after) - set(before))}"

    stdout = proc.stdout
    assert "rclone.conf" in stdout
    assert str(home / ".config" / "rclone" / "rclone.conf") in stdout
    assert str(hermes_home / "SOUL.md") in stdout
    assert "hermes config set model" in stdout
    assert "hermes config set cron.model" in stdout
    assert "hermes config set timezone UTC" in stdout

    for value in SECRET_VALUES:
        assert value not in stdout + proc.stderr, "dry-run echoed a secret value"
