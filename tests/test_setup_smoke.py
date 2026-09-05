"""Smoke tests for scripts/setup.sh: bash syntax, no-write --dry-run, and the
first-boot project-template seed (step 6) that never overwrites existing files."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_SH = REPO_ROOT / "scripts" / "setup.sh"

# Dummy fixture values — deliberately fake; the script must never echo them back.
# T2.12: no Drive/rclone env vars exist anymore — Drive access is $GAPI, in-container.
REQUIRED_ENV: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN": "test-telegram-token",
    "OPENROUTER_API_KEY": "test-openrouter-key",
}
SECRET_VALUES = tuple(REQUIRED_ENV.values())


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
        # Fresh seed target -> the dry-run plan is deterministic (all WOULD copy).
        "PROJECT_DATA_ROOT": str(tmp_path / "data"),
        **REQUIRED_ENV,
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
    assert "rclone" not in stdout.lower(), "setup.sh still plans an rclone step"
    assert str(hermes_home / "SOUL.md") in stdout
    assert "hermes config set model" in stdout
    assert "hermes config set cron.model" in stdout
    assert "hermes config set timezone UTC" in stdout
    # T1.7: the plan must include the plugin + toolsets runtime config (upstream gates
    # user plugins behind plugins.enabled; the kanban check_fn reads top-level toolsets).
    # Values print shell-quoted so a copy-pasted line survives bash globbing.
    assert "hermes config set plugins.enabled '[\"coordinator\"]'" in stdout
    assert 'hermes config set toolsets \'["hermes-cli", "kanban"]\'' in stdout
    # The CLI-absent fallback names the in-container variants (the plugin dir only
    # materializes inside the container's mount namespace).
    assert (
        "docker compose exec gateway hermes config set plugins.enabled '[\"coordinator\"]'"
        in stdout
    )
    assert (
        'docker compose exec gateway hermes config set toolsets \'["hermes-cli", "kanban"]\''
        in stdout
    )
    # T2.3: the seed step plans the first-boot copy of every template file (fresh
    # PROJECT_DATA_ROOT above -> nothing exists yet, so every file is a WOULD copy).
    assert "WOULD seed:" in stdout
    assert "WOULD copy: README.md" in stdout
    assert "WOULD copy: docs/index.md" in stdout
    assert "WOULD copy: inbox/.gitkeep" in stdout
    assert "exists (keep):" not in stdout

    for value in SECRET_VALUES:
        assert value not in stdout + proc.stderr, "dry-run echoed a secret value"


def _real_mode_env(tmp_path: Path) -> dict[str, str]:
    """Fixture env for a real run: every write target under tmp_path, valid config.yaml."""
    # The committed example validates against the schema, so step 2 passes with it.
    shutil.copyfile(REPO_ROOT / "config" / "config.example.yaml", tmp_path / "config.yaml")
    return {
        # Prepend the running interpreter's bin dir so bare `python` in the script
        # resolves to the project venv (coordinator importable) on any host.
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "HERMES_HOME": str(tmp_path / "hermes-home"),
        "PROJECT_DATA_ROOT": str(tmp_path / "data"),
        "CONFIG_YAML": str(tmp_path / "config.yaml"),
        "CONFIG_SCHEMA": str(REPO_ROOT / "config" / "config.schema.json"),
        **REQUIRED_ENV,
    }


def test_real_mode_seeds_project_template_without_overwriting(tmp_path: Path) -> None:
    """Real mode seeds project-template/ once; existing files are never overwritten."""
    project = tmp_path / "data" / "project"
    # Operator content that must survive the seed (the never-overwrite guarantee).
    project.mkdir(parents=True)
    (project / "README.md").write_text("# custom operator content\n", encoding="utf-8")

    env = _real_mode_env(tmp_path)
    first = subprocess.run(
        ["bash", str(SETUP_SH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    assert first.returncode == 0, f"real run failed:\n{first.stdout}\n{first.stderr}"
    assert "seeded:" in first.stdout

    assert (project / "README.md").read_text(encoding="utf-8") == "# custom operator content\n"
    assert (project / "docs" / "index.md").is_file()
    for empty_dir in ("inbox", "assets", "people", "journal", ".archive"):
        assert (project / empty_dir / ".gitkeep").is_file(), empty_dir

    def snapshot() -> dict[str, str]:
        """Content map of every seeded file, keyed by path relative to project/."""
        return {
            str(p.relative_to(project)): p.read_text(encoding="utf-8")
            for p in sorted(project.rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    del before["docs/index.md"]  # this file is edited below; compare the rest verbatim
    (project / "docs" / "index.md").write_text("# edited by the team\n", encoding="utf-8")
    second = subprocess.run(
        ["bash", str(SETUP_SH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    assert second.returncode == 0, f"re-run failed:\n{second.stdout}\n{second.stderr}"
    after = snapshot()
    assert after["docs/index.md"] == "# edited by the team\n"
    del after["docs/index.md"]
    assert after == before
