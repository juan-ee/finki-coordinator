"""Smoke tests for scripts/sync.sh (T2.1): bash syntax, env validation, argument gates.

Every tested invocation exercises a path that exits before any side effect (no
mkdir, no log append), so the suite never writes into the repo tree and never
invokes rclone: PATH is a tmp dir holding only the coreutils the script needs
(dirname, cat, date), which also proves the dry-run and missing-env paths work
without rclone installed.
"""

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SH = REPO_ROOT / "scripts" / "sync.sh"

# Resolved from the PARENT env on purpose: when subprocess gets env=, Python searches
# for the executable on the child PATH, and our hermetic child PATH deliberately has
# no bash in it — only the coreutils sync.sh itself needs.
BASH = shutil.which("bash")
assert BASH is not None, "bash missing on the test host"

# Dummy fixture values — deliberately fake (AGENTS.md rule 7). Per proposal §2 both
# RCLONE_* vars are non-secret ("remote name" / "sync scope"), so the wrapper's dry-run
# may print them: the planned command is the point of a dry run. Real secrets (tokens,
# client id/secret) are never read by scripts/sync.sh at all.
FAKE_REMOTE = "test-remote:"
FAKE_FOLDER_ID = "test-folder-id"


def _fake_bin(tmp_path: Path) -> Path:
    """PATH dir with only the coreutils sync.sh needs — rclone is never reachable."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("dirname", "cat", "date"):
        tool_path = shutil.which(tool)
        assert tool_path is not None, f"{tool} missing on the test host"
        (bin_dir / tool).symlink_to(tool_path)
    return bin_dir


def _base_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """Explicit hermetic env (no os.environ inheritance): fake-bin PATH, fake HOME."""
    return {
        "PATH": str(_fake_bin(tmp_path)),
        "HOME": str(tmp_path / "home"),
        **extra,
    }


def _run_sync(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run scripts/sync.sh with an explicit environment and captured output."""
    return subprocess.run(
        [BASH, str(SYNC_SH), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_sync_sh_passes_bash_syntax_check() -> None:
    """scripts/sync.sh exists and parses cleanly under the bash -n syntax check."""
    assert SYNC_SH.exists(), f"missing {SYNC_SH}"

    proc = subprocess.run(
        [BASH, "-n", str(SYNC_SH)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, f"bash -n failed:\n{proc.stderr}"


def test_dry_run_without_env_vars_exits_nonzero_with_actionable_message(tmp_path: Path) -> None:
    """Dry run with no RCLONE_* env exits non-zero and names the variable to set."""
    proc = _run_sync("--dry-run", env=_base_env(tmp_path))

    assert proc.returncode != 0
    assert "RCLONE_REMOTE" in proc.stderr
    assert "env.example" in proc.stderr, "error must point at where to set the variable"
    assert "usage" in proc.stderr.lower(), "usage block must ride along on errors"


def test_dry_run_refuses_resync_without_acknowledgement(tmp_path: Path) -> None:
    """--resync without --i-know-what-im-doing is refused (never run blind, proposal §3)."""
    proc = _run_sync(
        "--dry-run",
        "--resync",
        env=_base_env(tmp_path, RCLONE_REMOTE=FAKE_REMOTE),
    )

    assert proc.returncode != 0
    assert "--i-know-what-im-doing" in proc.stderr


def test_dry_run_resync_with_acknowledgement_plans_resync_command(tmp_path: Path) -> None:
    """--resync with the acknowledgement proceeds and plans the --resync flag."""
    proc = _run_sync(
        "--dry-run",
        "--resync",
        "--i-know-what-im-doing",
        env=_base_env(tmp_path, RCLONE_REMOTE=FAKE_REMOTE),
    )

    assert proc.returncode == 0
    assert "--resync" in proc.stdout


def test_dry_run_boot_flag_is_accepted_and_tagged(tmp_path: Path) -> None:
    """--boot --dry-run exits 0 and marks the run as a boot-time sync."""
    proc = _run_sync(
        "--dry-run",
        "--boot",
        env=_base_env(tmp_path, RCLONE_REMOTE=FAKE_REMOTE),
    )

    assert proc.returncode == 0
    assert "boot" in proc.stdout.lower()


def test_dry_run_plans_bisync_between_remote_and_local_mirror(tmp_path: Path) -> None:
    """Dry run prints the bisync command pairing RCLONE_REMOTE with data/project/."""
    proc = _run_sync(
        "--dry-run",
        env=_base_env(
            tmp_path,
            RCLONE_REMOTE=FAKE_REMOTE,
            RCLONE_ROOT_FOLDER_ID=FAKE_FOLDER_ID,
        ),
    )

    assert proc.returncode == 0
    assert "DRY RUN" in proc.stdout
    assert "bisync" in proc.stdout
    assert FAKE_REMOTE in proc.stdout
    assert "data/project" in proc.stdout
    assert "--drive-root-folder-id" in proc.stdout
    assert FAKE_FOLDER_ID in proc.stdout


def test_dry_run_without_folder_id_omits_root_folder_flag(tmp_path: Path) -> None:
    """RCLONE_ROOT_FOLDER_ID is optional: unset means no --drive-root-folder-id flag."""
    proc = _run_sync("--dry-run", env=_base_env(tmp_path, RCLONE_REMOTE=FAKE_REMOTE))

    assert proc.returncode == 0
    assert "--drive-root-folder-id" not in proc.stdout


def test_real_mode_without_rclone_on_path_fails_with_actionable_message(tmp_path: Path) -> None:
    """Real (non-dry) mode with no rclone reachable exits non-zero and names rclone."""
    proc = _run_sync(env=_base_env(tmp_path, RCLONE_REMOTE=FAKE_REMOTE))

    assert proc.returncode != 0
    assert "rclone" in proc.stderr.lower()


def test_unknown_flag_prints_usage_and_fails(tmp_path: Path) -> None:
    """An unknown flag exits non-zero and prints the usage block naming the flag."""
    proc = _run_sync("--bogus", env=_base_env(tmp_path, RCLONE_REMOTE=FAKE_REMOTE))

    assert proc.returncode != 0
    assert "--bogus" in proc.stderr
    assert "usage" in proc.stderr.lower()
