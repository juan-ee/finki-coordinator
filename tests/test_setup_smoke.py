"""Smoke tests for scripts/setup.sh: bash syntax, no-write --dry-run, the
first-boot project-template seed (step 7) that never overwrites existing files,
the runtime skill-store install (T2.25) that always overwrites, and the Google
token-permission check + exec-user guard (T2.26).

The T2.26 repair/remedy tests pin NON-ROOT operator behavior (root takes the
script's chown+chmod repair branch), so they skip under a root pytest."""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_SH = REPO_ROOT / "scripts" / "setup.sh"

# Dummy fixture values — deliberately fake; the script must never echo them back.
# T2.12: no Drive/rclone env vars exist anymore — Drive access is $GAPI, in-container.
REQUIRED_ENV: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN": "test-telegram-token",
    "OPENROUTER_API_KEY": "test-openrouter-key",
}
SECRET_VALUES = tuple(REQUIRED_ENV.values())

# T2.25: the template-owned skills installed into the runtime skill store, one
# directory per skill under $HERMES_HOME/skills/coordinator-<name>/.
SKILL_NAMES = ("check-in", "digest", "knowledge", "schedules")


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
    # T2.25: the plan must include the runtime skill-store install action (one
    # coordinator-<name>/SKILL.md destination per template skill).
    for name in SKILL_NAMES:
        assert str(hermes_home / "skills" / f"coordinator-{name}" / "SKILL.md") in stdout, (
            f"dry-run plan misses the coordinator-{name} install"
        )
    # Renumber precedent (T1.7/T2.12/T2.25/T2.26/T2.30): every step banner carries the
    # current total, so the printed plan can never contradict the docstring's list.
    for n in range(1, 10):
        assert f"[{n}/9]" in stdout, f"step banner [{n}/9] missing from the dry-run plan"

    # T2.26 (b)/(f): the plan must carry the Google-token permission check and the
    # exec-user guard (docker compose exec defaults to root in this container).
    assert "== [8/9] Google token permissions" in stdout
    assert "google_token.json" in stdout
    assert "docker compose exec --user" in stdout
    assert "ROOT" in stdout

    # T2.30: the plan must carry the site-rebuild crontab line (every 15 min, UTC —
    # dumb and LLM-free), installed idempotently by marker.
    assert "== [9/9] Site rebuild cron" in stdout
    assert "*/15 * * * *" in stdout and "site-build" in stdout

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
        """Content map of every seeded file, keyed by path relative to project/.

        T2.29: docs/ is its own git repo — .git internals are runtime VCS state,
        not seeded content, and are excluded from the byte-comparison.
        """
        return {
            str(p.relative_to(project)): p.read_text(encoding="utf-8")
            for p in sorted(project.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(project).parts
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


def _installed_skill_paths(hermes_home: Path) -> dict[str, Path]:
    """Map each skill name to its installed runtime-store path."""
    return {
        name: hermes_home / "skills" / f"coordinator-{name}" / "SKILL.md" for name in SKILL_NAMES
    }


def test_real_mode_installs_all_skills_with_frontmatter(tmp_path: Path) -> None:
    """Real mode lands every skill at coordinator-<name>/SKILL.md under HERMES_HOME,
    each carrying the three Hermes-style frontmatter keys (name/description/category)."""
    env = _real_mode_env(tmp_path)
    proc = subprocess.run(
        ["bash", str(SETUP_SH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, f"real run failed:\n{proc.stdout}\n{proc.stderr}"

    for name, installed in _installed_skill_paths(tmp_path / "hermes-home").items():
        assert installed.is_file(), f"missing install for coordinator-{name}"
        text = installed.read_text(encoding="utf-8")
        assert text.startswith("---"), f"no frontmatter header: {installed}"
        header = text.split("---", 2)[1]
        assert f"name: coordinator-{name}" in header, installed
        assert "description: " in header, installed
        assert "category: productivity" in header, installed
        assert header.strip(), f"empty frontmatter body: {installed}"


def test_real_mode_skill_install_overwrites_mutated_files(tmp_path: Path) -> None:
    """The template is authoritative: every run re-copies the skills, so a mutated
    installed file is replaced (unlike the project seed, which never overwrites)."""
    env = _real_mode_env(tmp_path)
    first = subprocess.run(
        ["bash", str(SETUP_SH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    assert first.returncode == 0, f"first run failed:\n{first.stdout}\n{first.stderr}"

    installed = _installed_skill_paths(tmp_path / "hermes-home")["knowledge"]
    assert installed.is_file(), "knowledge skill not installed by the first run"
    installed.write_text("# mutated after install — kb_sync copy lives here\n", encoding="utf-8")

    second = subprocess.run(
        ["bash", str(SETUP_SH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    assert second.returncode == 0, f"re-run failed:\n{second.stdout}\n{second.stderr}"

    source = (REPO_ROOT / "prompts" / "skills" / "knowledge" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert installed.read_text(encoding="utf-8") == source, "template did not win"


# --- T2.26 (b): Google-token permission check (step 8/8) --------------------
#
# The 2026-09-05 incident: ~/.hermes/google_token.json flipped to root-owned, and
# every token refresh write by the container's runtime uid failed. setup.sh runs
# on the host with operator privileges: it checks the token against the container
# runtime uid and repairs it when it can (sudo remedy otherwise).


def _write_token(hermes_home: Path, mode: int) -> Path:
    """A fake Google token at the path setup.sh checks, with the given mode."""
    hermes_home.mkdir(parents=True, exist_ok=True)
    token = hermes_home / "google_token.json"
    token.write_text('{"refresh_token": "fixture"}', encoding="utf-8")
    token.chmod(mode)
    return token


def _run_setup(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run setup.sh (real mode) with the given fixture env."""
    return subprocess.run(
        ["bash", str(SETUP_SH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )


_requires_non_root = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root takes setup.sh's chown+chmod repair branch, not the operator paths",
)


@_requires_non_root
def test_real_mode_reports_ok_token_untouched(tmp_path: Path) -> None:
    """A writable token (owner == container uid, mode 600) prints ok and is untouched."""
    token = _write_token(tmp_path / "hermes-home", 0o600)

    proc = _run_setup(_real_mode_env(tmp_path))

    assert proc.returncode == 0, f"real run failed:\n{proc.stdout}\n{proc.stderr}"
    assert "ok:" in proc.stdout
    assert stat.S_IMODE(token.stat().st_mode) == 0o600


@_requires_non_root
def test_real_mode_repairs_mode_mangled_token_owned_by_container_uid(tmp_path: Path) -> None:
    """Operator-owned token with the write bit lost: real mode chmod-repairs it."""
    token = _write_token(tmp_path / "hermes-home", 0o444)

    proc = _run_setup(_real_mode_env(tmp_path))

    assert proc.returncode == 0, f"real run failed:\n{proc.stdout}\n{proc.stderr}"
    assert "repaired" in proc.stdout
    assert stat.S_IMODE(token.stat().st_mode) == 0o600


@_requires_non_root
def test_real_mode_prints_sudo_remedy_for_foreign_owned_token_and_touches_nothing(
    tmp_path: Path,
) -> None:
    """The incident shape: a token the container uid does not own gets the exact
    sudo chown/chmod remedy; a non-root operator run repairs nothing itself."""
    token = _write_token(tmp_path / "hermes-home", 0o644)
    env = _real_mode_env(tmp_path)
    env["HERMES_UID"] = "4242"  # a container uid that owns nothing here
    env["HERMES_GID"] = "4242"

    proc = _run_setup(env)

    assert proc.returncode == 0, f"real run failed:\n{proc.stdout}\n{proc.stderr}"
    assert "sudo chown 4242:4242" in proc.stdout
    assert str(token) in proc.stdout
    assert "chmod 600" in proc.stdout
    assert stat.S_IMODE(token.stat().st_mode) == 0o644, "non-root run must not repair"


def test_dry_run_prints_remedy_but_never_touches_a_broken_token(tmp_path: Path) -> None:
    """Dry-run names the fix for a broken token and writes nothing (mode intact)."""
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    token = _write_token(hermes_home, 0o444)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "HERMES_HOME": str(hermes_home),
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

    assert proc.returncode == 0, f"dry-run failed:\n{proc.stdout}\n{proc.stderr}"
    assert "chmod 600" in proc.stdout
    assert str(token) in proc.stdout
    assert stat.S_IMODE(token.stat().st_mode) == 0o444, "dry-run must not repair"
    assert _snapshot(tmp_path) == before, "dry-run wrote under tmp roots"


def test_real_mode_absent_token_prints_absent_not_error(tmp_path: Path) -> None:
    """Pre-OAuth (no token yet): the check reports the token absent, setup succeeds.
    (The ':' pins the check's own line — the tmp dir name contains 'absent'.)"""
    proc = _run_setup(_real_mode_env(tmp_path))

    assert proc.returncode == 0, f"real run failed:\n{proc.stdout}\n{proc.stderr}"
    assert "absent:" in proc.stdout


def test_real_mode_skips_check_on_non_numeric_container_uid(tmp_path: Path) -> None:
    """A non-numeric HERMES_UID must not abort the script in the set -u arithmetic
    (the old form died cryptically at step 8/8, after steps 1-7 had already
    applied); the step warns, skips, and still prints the exec-user guard."""
    env = _real_mode_env(tmp_path)
    env["HERMES_UID"] = "abc"

    proc = _run_setup(env)

    assert proc.returncode == 0, f"real run failed:\n{proc.stdout}\n{proc.stderr}"
    assert "HERMES_UID/HERMES_GID must be numeric" in proc.stdout
    assert "docker compose exec --user" in proc.stdout


# --- T2.29: the local KB workspace — data/project/docs/ is its own git repo ----------


def _git(
    docs: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one git command inside the seeded docs repo (captured, no failure raise).

    Pass env= to query git through the fixture environment: an unset env leaks the
    real operator's global gitconfig, which would mask exactly the behavior under
    test (setup.sh must NOT override an operator identity with a local one).
    """
    return subprocess.run(
        ["git", "-C", str(docs), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_real_mode_initializes_docs_git_repo_with_baseline_commit(tmp_path: Path) -> None:
    """Real mode git-inits data/project/docs/ (invariant 1: local history for the
    record), sets a local identity when the operator has none, and commits the
    seeded content as the baseline."""
    env = _real_mode_env(tmp_path)
    # No global git identity in the fixture env: the repo's LOCAL identity is what
    # the test pins (setup.sh must make the repo committable on a bare machine).
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "empty-gitconfig")
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"

    proc = _run_setup(env)

    assert proc.returncode == 0, f"real run failed:\n{proc.stdout}\n{proc.stderr}"
    docs = tmp_path / "data" / "project" / "docs"
    assert (docs / ".git").is_dir(), "docs/ was not git-initialized"
    assert "git: initialized" in proc.stdout  # the exact git-setup line, not any mention

    identity = _git(docs, "config", "user.name")
    assert identity.returncode == 0 and identity.stdout.strip(), "no local git identity"
    tracked = _git(docs, "ls-files")
    assert "index.md" in tracked.stdout, "seeded docs not committed"
    log = _git(docs, "log", "--oneline")
    assert log.returncode == 0
    assert len(log.stdout.strip().splitlines()) == 1, "expected exactly one baseline commit"
    status = _git(docs, "status", "--porcelain")
    assert status.stdout.strip() == "", "baseline commit left the repo dirty"


def test_real_mode_docs_git_init_is_idempotent(tmp_path: Path) -> None:
    """A re-run neither re-initializes the repo nor adds a second baseline commit."""
    env = _real_mode_env(tmp_path)
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "empty-gitconfig")
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    first = _run_setup(env)
    assert first.returncode == 0, first.stderr
    docs = tmp_path / "data" / "project" / "docs"

    second = _run_setup(env)

    assert second.returncode == 0, second.stderr
    log = _git(docs, "log", "--oneline")
    assert log.returncode == 0
    assert len(log.stdout.strip().splitlines()) == 1, "re-run added commits"


def test_real_mode_keeps_operator_git_identity_when_present(tmp_path: Path) -> None:
    """A global operator identity is respected: setup.sh never overrides it locally.

    HOME isolation (not GIT_CONFIG_GLOBAL — older git versions ignore it): the
    fixture HOME gains an operator .gitconfig, and setup.sh must leave it in force.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    operator_cfg = tmp_path / "operator-gitconfig"
    operator_cfg.write_text(
        "[user]\n\tname = Operator\n\temail = operator@example.com\n", encoding="utf-8"
    )
    env = _real_mode_env(tmp_path)
    env["HOME"] = str(home)
    # Both identity channels carry the operator identity, so the test is independent
    # of the local git version (GIT_CONFIG_GLOBAL needs git >= 2.32; $HOME/.gitconfig
    # is the universal fallback).
    (home / ".gitconfig").write_text(operator_cfg.read_text(encoding="utf-8"))
    env["GIT_CONFIG_GLOBAL"] = str(operator_cfg)
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"

    proc = _run_setup(env)

    assert proc.returncode == 0, proc.stderr
    docs = tmp_path / "data" / "project" / "docs"
    name = _git(docs, "config", "user.name", env=env)
    assert name.stdout.strip() == "Operator", "operator identity was overridden"


def test_dry_run_plans_docs_git_repo_but_writes_nothing(tmp_path: Path) -> None:
    """Dry-run names the git-init plan; nothing is created (no .git anywhere)."""
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "HERMES_HOME": str(hermes_home),
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

    assert proc.returncode == 0, f"dry-run failed:\n{proc.stdout}\n{proc.stderr}"
    assert "git repo" in proc.stdout, "dry-run plan misses the docs git-init step"
    assert _snapshot(tmp_path) == before, "dry-run wrote under tmp roots"


# --- T2.30: the site-rebuild crontab line (step 9/9) ------------------------------------


def _site_cron_env(tmp_path: Path) -> dict[str, str]:
    """Real-mode env with a stateful crontab shim on PATH.

    The shim mimics the operator interface: '-l' prints the stored crontab (or the
    standard 'no crontab for <user>' stderr + exit 1 when none exists — setup.sh
    must treat exactly that case as empty, never any other failure); installs
    (stdin) replace the store. Invocations and installed lines hit CRONTAB_LOG.
    """
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "crontab"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'DB="$CRONTAB_DB"\n'
        'echo "ARGS: $*" >> "$CRONTAB_LOG"\n'
        'if [[ "$1" == "-l" ]]; then\n'
        '  if [[ -f "$DB" ]]; then cat "$DB"; exit 0; fi\n'
        '  echo "no crontab for $USER" >&2\n'
        "  exit 1\n"
        "fi\n"
        'cat > "$DB"\n'
        'echo "--- installed ---" >> "$CRONTAB_LOG"\n'
        'cat "$DB" >> "$CRONTAB_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    (tmp_path / "crontab-db").touch()
    env = _real_mode_env(tmp_path)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
    env["CRONTAB_LOG"] = str(tmp_path / "crontab.log")
    env["CRONTAB_DB"] = str(tmp_path / "crontab-db")
    return env


def test_real_mode_installs_the_site_rebuild_crontab_line(tmp_path: Path) -> None:
    """Real mode appends the marker + */15 site-build line through crontab (shimmed)."""
    env = _site_cron_env(tmp_path)

    proc = _run_setup(env)

    assert proc.returncode == 0, f"real run failed:\n{proc.stdout}\n{proc.stderr}"
    log = Path(env["CRONTAB_LOG"]).read_text(encoding="utf-8")
    assert "ARGS: -l" in log, "setup.sh must read the existing crontab first"
    installed = log.split("ARGS:", 1)[-1]
    assert "*/15 * * * *" in installed, "the 15-minute schedule is missing"
    assert "site-build" in installed, "the rebuild command is missing"
    assert "hermes-coordinator" in installed, "the idempotency marker is missing"


def test_real_mode_site_cron_install_is_idempotent(tmp_path: Path) -> None:
    """A second run must not duplicate the cron line (marker-matched, shim records all)."""
    env = _site_cron_env(tmp_path)
    first = _run_setup(env)
    assert first.returncode == 0, first.stderr

    second = _run_setup(env)

    assert second.returncode == 0, second.stderr
    log = Path(env["CRONTAB_LOG"]).read_text(encoding="utf-8")
    # The stateful shim feeds the stored crontab back on -l, so the second run must
    # find the marker and install nothing new: exactly one install in the log.
    installs = log.count("*/15 * * * *")
    assert installs == 1, f"expected exactly one cron install, got {installs}"
