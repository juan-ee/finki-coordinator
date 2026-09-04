"""Offline smoke tests for scripts/allow.sh (T2.7): the operator door script.

allow.sh edits TELEGRAM_ALLOWED_USERS in the gitignored .env and applies with
docker compose up -d (never restart). Tests never touch a real .env: they run the
script against a fixture file via ALLOW_ENV_FILE, and real mode is exercised with a
fake docker on PATH that records its argv (T2.1 fake-bin precedent) — the suite
stays offline and asserts the exact compose invocation.
"""

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "allow.sh"

# Fixture secrets: these values must NEVER appear in the script output (rule 7).
SECRETS = {
    "TELEGRAM_BOT_TOKEN": "tok-fixture-do-not-leak-9f8e7d",
    "OPENROUTER_API_KEY": "sk-or-fixture-do-not-leak-4b3a2c",
}


def _write_env(path: Path, allow_value: str | None) -> None:
    """Write a fixture .env: fake secrets + a template-shaped allowlist line."""
    lines = [f"{key}={value}" for key, value in SECRETS.items()]
    if allow_value is not None:
        lines.append(f"TELEGRAM_ALLOWED_USERS={allow_value}  # comma-separated IDs")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _fake_bin(tmp_path: Path, record: Path) -> Path:
    """Return a PATH dir whose fake docker records its argv into record and exits 0."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        f'#!/usr/bin/env bash\nfor a in "$@"; do\n  echo "$a" >> {record}\ndone\nexit 0\n',
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run(
    args: list[str],
    env_file: Path,
    *,
    fake_path: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run allow.sh against a fixture env file; secret values scrubbed from child env."""
    env = {key: value for key, value in os.environ.items() if value not in SECRETS.values()}
    env["ALLOW_ENV_FILE"] = str(env_file)
    if fake_path is not None:
        env["PATH"] = fake_path + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_passes_bash_syntax_check() -> None:
    """bash -n accepts the script (syntax guard, no execution)."""
    proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)

    assert proc.returncode == 0, proc.stderr


def test_dry_run_lists_ids_names_up_d_and_writes_nothing(tmp_path: Path) -> None:
    """--dry-run lists missing IDs, names up -d, never suggests a restart invocation."""
    env_file = tmp_path / ".env"
    _write_env(env_file, "111")
    before = env_file.read_bytes()

    proc = _run(["--dry-run", "111", "222", "333"], env_file)

    assert proc.returncode == 0, proc.stderr
    assert env_file.read_bytes() == before, "dry-run must not write"
    assert "222" in proc.stdout and "333" in proc.stdout, "missing IDs must be listed"
    assert "docker compose up -d" in proc.stdout
    # Never SUGGEST a restart invocation; the rationale line may still name the word.
    run_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("WOULD run:")]
    assert run_lines == ["WOULD run:  docker compose up -d"], "must suggest only up -d"
    assert "restart reuses the env frozen at container creation" in proc.stdout


def test_real_mode_appends_missing_ids_and_runs_up_d(tmp_path: Path) -> None:
    """Real mode appends only the missing IDs, keeps secrets, runs exactly up -d."""
    env_file = tmp_path / ".env"
    _write_env(env_file, "111")
    record = tmp_path / "docker-argv.txt"

    proc = _run(["111", "222", "333"], env_file, fake_path=str(_fake_bin(tmp_path, record)))

    assert proc.returncode == 0, proc.stderr
    content = env_file.read_text(encoding="utf-8")
    assert "TELEGRAM_ALLOWED_USERS=111,222,333" in content
    for secret in SECRETS.values():  # the untouched lines are still intact
        assert secret in content
    argv = record.read_text(encoding="utf-8").splitlines()
    assert argv == ["compose", "up", "-d"], f"expected up -d, got {argv}"


def test_real_mode_output_carries_the_never_restart_rationale(tmp_path: Path) -> None:
    """Real mode states why up -d and never restart (spec: the output says so)."""
    env_file = tmp_path / ".env"
    _write_env(env_file, "111")
    record = tmp_path / "docker-argv.txt"

    proc = _run(["222"], env_file, fake_path=str(_fake_bin(tmp_path, record)))

    assert proc.returncode == 0, proc.stderr
    assert "docker compose up -d" in proc.stdout
    assert "restart reuses the env frozen at container creation" in proc.stdout
    applied = [ln for ln in proc.stdout.splitlines() if "applied:" in ln]
    assert applied, "the applied line must name the command"
    assert all("restart" not in ln.lower() for ln in applied), "applied line never names restart"


def test_real_mode_preserves_every_other_byte(tmp_path: Path) -> None:
    """Only the allowlist line changes; every other line is byte-identical."""
    env_file = tmp_path / ".env"
    lines = [
        "TELEGRAM_BOT_TOKEN=tok-fixture-do-not-leak-9f8e7d",
        'ODD_KEY=weird value "quoted" $dollar',
        "TELEGRAM_ALLOWED_USERS=111  # comma-separated IDs",
        "# a full-line comment stays",
        "",
    ]
    env_file.write_text("\n".join(lines), encoding="utf-8")
    record = tmp_path / "docker-argv.txt"

    proc = _run(["222"], env_file, fake_path=str(_fake_bin(tmp_path, record)))

    assert proc.returncode == 0, proc.stderr
    after = env_file.read_text(encoding="utf-8").splitlines()
    assert after[0] == lines[0] and after[1] == lines[1]
    assert after[2] == "TELEGRAM_ALLOWED_USERS=111,222  # comma-separated IDs"
    assert after[3] == lines[3]
    expected = list(lines[:4])  # every other line byte-identical, order kept
    expected[2] = "TELEGRAM_ALLOWED_USERS=111,222  # comma-separated IDs"
    assert after == expected
    assert env_file.read_text(encoding="utf-8").endswith("\n")


def test_second_run_is_a_noop_and_skips_compose(tmp_path: Path) -> None:
    """Idempotency: re-running the same IDs changes no bytes and does not call docker."""
    env_file = tmp_path / ".env"
    _write_env(env_file, "111")
    record = tmp_path / "docker-argv.txt"
    fake = _fake_bin(tmp_path, record)

    first = _run(["111", "222"], env_file, fake_path=str(fake))
    assert first.returncode == 0, first.stderr
    after_first = env_file.read_bytes()
    assert record.exists()  # first run applied
    record.unlink()

    second = _run(["111", "222"], env_file, fake_path=str(fake))

    assert second.returncode == 0, second.stderr
    assert env_file.read_bytes() == after_first, "second run must change nothing"
    assert not record.exists(), "no-op run must not invoke docker"
    assert "up -d" not in second.stdout


def test_non_numeric_id_is_rejected_and_env_untouched(tmp_path: Path) -> None:
    """Non-numeric arguments exit 2 with a usage hint; .env stays byte-identical."""
    env_file = tmp_path / ".env"
    _write_env(env_file, "111")
    before = env_file.read_bytes()

    proc = _run(["12ab"], env_file)

    assert proc.returncode == 2
    assert "usage" in (proc.stdout + proc.stderr).lower()
    assert env_file.read_bytes() == before


def test_no_ids_is_a_usage_error(tmp_path: Path) -> None:
    """Running with no IDs exits 2 with usage (the dry-run flag alone is not an ID)."""
    env_file = tmp_path / ".env"
    _write_env(env_file, "111")

    proc = _run(["--dry-run"], env_file)

    assert proc.returncode == 2
    assert "usage" in (proc.stdout + proc.stderr).lower()


def test_absent_allowlist_line_is_created(tmp_path: Path) -> None:
    """A .env without the key gains the line at EOF; bytes above it are preserved."""
    env_file = tmp_path / ".env"
    _write_env(env_file, None)
    before_lines = env_file.read_text(encoding="utf-8").splitlines()
    record = tmp_path / "docker-argv.txt"

    proc = _run(["555"], env_file, fake_path=str(_fake_bin(tmp_path, record)))

    assert proc.returncode == 0, proc.stderr
    after = env_file.read_text(encoding="utf-8").splitlines()
    assert "TELEGRAM_ALLOWED_USERS=555" in after
    assert after[: len(before_lines)] == before_lines
    assert record.read_text(encoding="utf-8").splitlines() == ["compose", "up", "-d"]


def test_absent_key_without_trailing_newline_appends_clean_line(tmp_path: Path) -> None:
    """Key absent + no final newline: no leading blank line, real new line, bytes kept."""
    env_file = tmp_path / ".env"
    body = (
        "TELEGRAM_BOT_TOKEN=tok-fixture-do-not-leak-9f8e7d\n"
        "OPENROUTER_API_KEY=sk-or-fixture-do-not-leak-4b3a2c"  # no trailing newline
    )
    env_file.write_text(body, encoding="utf-8")
    record = tmp_path / "docker-argv.txt"

    proc = _run(["555"], env_file, fake_path=str(_fake_bin(tmp_path, record)))

    assert proc.returncode == 0, proc.stderr
    after = env_file.read_bytes()
    assert not after.startswith(b"\n"), "no leading blank line before the original bytes"
    assert after == body.encode("utf-8") + b"\nTELEGRAM_ALLOWED_USERS=555\n"


def test_empty_allowlist_value_appends_and_is_idempotent(tmp_path: Path) -> None:
    """TELEGRAM_ALLOWED_USERS= (nothing after '='): IDs append; rerun changes nothing."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=tok-fixture-do-not-leak-9f8e7d\n"
        "TELEGRAM_ALLOWED_USERS=\n"
        "OPENROUTER_API_KEY=sk-or-fixture-do-not-leak-4b3a2c\n",
        encoding="utf-8",
    )
    record = tmp_path / "docker-argv.txt"
    fake = _fake_bin(tmp_path, record)

    proc = _run(["222", "333"], env_file, fake_path=str(fake))

    assert proc.returncode == 0, proc.stderr
    after_first = env_file.read_text(encoding="utf-8")
    assert "TELEGRAM_ALLOWED_USERS=222,333\n" in after_first
    for secret in SECRETS.values():
        assert secret in after_first
    assert record.read_text(encoding="utf-8").splitlines() == ["compose", "up", "-d"]
    record.unlink()

    second = _run(["222", "333"], env_file, fake_path=str(fake))

    assert second.returncode == 0, second.stderr
    assert env_file.read_text(encoding="utf-8") == after_first, "rerun must change nothing"
    assert not record.exists(), "rerun must not invoke docker"


def test_key_on_first_line_is_replaced(tmp_path: Path) -> None:
    """Allowlist on line 1: no head slice runs (BSD head rejects 'head -n 0')."""
    env_file = tmp_path / ".env"
    lines = [
        "TELEGRAM_ALLOWED_USERS=111  # comma-separated IDs",
        "TELEGRAM_BOT_TOKEN=tok-fixture-do-not-leak-9f8e7d",
        "OPENROUTER_API_KEY=sk-or-fixture-do-not-leak-4b3a2c",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    record = tmp_path / "docker-argv.txt"

    proc = _run(["222"], env_file, fake_path=str(_fake_bin(tmp_path, record)))

    assert proc.returncode == 0, proc.stderr
    expected = list(lines)
    expected[0] = "TELEGRAM_ALLOWED_USERS=111,222  # comma-separated IDs"
    assert env_file.read_text(encoding="utf-8").splitlines() == expected


def test_duplicate_cli_args_are_counted_once(tmp_path: Path) -> None:
    """Repeated CLI args are de-duplicated: '222 222' appends 222 exactly once."""
    env_file = tmp_path / ".env"
    _write_env(env_file, "111")
    record = tmp_path / "docker-argv.txt"

    proc = _run(["222", "222", "333"], env_file, fake_path=str(_fake_bin(tmp_path, record)))

    assert proc.returncode == 0, proc.stderr
    content = env_file.read_text(encoding="utf-8")
    assert "TELEGRAM_ALLOWED_USERS=111,222,333" in content
    assert "222,222" not in content, "a repeated requested ID must not be appended twice"


def test_missing_env_file_fails_actionably(tmp_path: Path) -> None:
    """No .env at the configured path: actionable error, exit 1, nothing created."""
    env_file = tmp_path / "nonexistent.env"

    proc = _run(["111"], env_file)

    assert proc.returncode == 1
    assert ".env.example" in (proc.stdout + proc.stderr)
    assert not env_file.exists()


def test_never_prints_other_env_values(tmp_path: Path) -> None:
    """Secret values from the fixture never appear in stdout or stderr."""
    env_file = tmp_path / ".env"
    _write_env(env_file, "111")
    record = tmp_path / "docker-argv.txt"

    proc = _run(["222"], env_file, fake_path=str(_fake_bin(tmp_path, record)))

    combined = proc.stdout + proc.stderr
    for secret in SECRETS.values():
        assert secret not in combined, "a fixture secret leaked into output"
    assert proc.returncode == 0, proc.stderr


def test_duplicate_key_line_is_rejected(tmp_path: Path) -> None:
    """An ambiguous .env (two allowlist lines) fails loudly instead of guessing."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_ALLOWED_USERS=111\nTELEGRAM_ALLOWED_USERS=222\n", encoding="utf-8"
    )
    before = env_file.read_bytes()

    proc = _run(["333"], env_file)

    assert proc.returncode == 1
    assert env_file.read_bytes() == before


def test_allow_temp_files_are_gitignored_and_env_example_stays_tracked() -> None:
    """The allow.sh mktemp scratch (.env.allow*) is ignored; .env.example stays tracked."""

    def is_ignored(repo_path: str) -> bool:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", repo_path],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0

    assert is_ignored(".env"), ".env must stay ignored"
    assert is_ignored(".env.allowk3XyZ9"), "the mktemp scratch file must be ignored"
    assert not is_ignored(".env.example"), ".env.example must stay tracked"
