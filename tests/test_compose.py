"""Compose template tests (T0.14): offline render + HERMES_REF pin drift guard.

`docker compose config` must validate the template offline with no .env and no
data/ directory present, and the commit pinned in docker/HERMES_REF must appear
verbatim in the rendered build context (drift guard: the pin lives in BOTH the
pin file and docker-compose.yml's build URL — they move together).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_REF_FILE = REPO_ROOT / "docker" / "HERMES_REF"

# Every variable docker-compose.yml passes through to the container. These are
# scrubbed from the subprocess environment so a developer's exported secrets can
# never leak into the rendered config (and therefore into this test's output).
PASSTHROUGH_VARS = frozenset(
    {
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_CHATS",
        "TELEGRAM_GROUP_ALLOWED_USERS",
        "OPENROUTER_API_KEY",
        "GOOGLE_DRIVE_CLIENT_ID",
        "GOOGLE_DRIVE_CLIENT_SECRET",
        "GOOGLE_DRIVE_REFRESH_TOKEN",
        "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON",
        "RCLONE_REMOTE",
        "RCLONE_ROOT_FOLDER_ID",
    }
)

docker_missing = shutil.which("docker") is None
pytestmark = pytest.mark.skipif(docker_missing, reason="docker CLI not available")


def _render_config() -> subprocess.CompletedProcess[str]:
    """Render `docker compose config` from the repo root, offline and secret-free."""
    env = {k: v for k, v in os.environ.items() if k not in PASSTHROUGH_VARS}
    # --env-file /dev/null: a developer's local .env must never be interpolated
    # into the rendered output — secrets stay null and the render is deterministic.
    return subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_compose_config_exits_zero() -> None:
    """`docker compose config` exits 0 offline with no .env and no data/ present."""
    proc = _render_config()

    assert proc.returncode == 0, f"compose config failed:\n{proc.stderr}"


def test_hermes_ref_appears_in_rendered_config() -> None:
    """The docker/HERMES_REF pin appears verbatim in the rendered config output."""
    ref = HERMES_REF_FILE.read_text(encoding="utf-8").strip()
    proc = _render_config()

    assert proc.returncode == 0, f"compose config failed:\n{proc.stderr}"
    assert ref, f"{HERMES_REF_FILE} is empty"
    assert ref in proc.stdout, f"pinned ref {ref!r} not in rendered config"
