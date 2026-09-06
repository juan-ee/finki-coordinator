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
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_REF_FILE = REPO_ROOT / "docker" / "HERMES_REF"

# Every variable docker-compose.yml passes through to the container (T2.12 contract:
# Drive access is skill-managed via $GAPI — no Drive/rclone env vars exist anymore).
# These are scrubbed from the subprocess environment so a developer's exported secrets
# can never leak into the rendered config (and therefore into this test's output).
PASSTHROUGH_VARS = frozenset(
    {
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_CHATS",
        "TELEGRAM_GROUP_ALLOWED_USERS",
        "OPENROUTER_API_KEY",
    }
)

# Legacy v5 Drive/rclone variable NAMES: nothing interpolates them anymore, but a
# developer who still exports them must not see them leak into any rendered output.
LEGACY_DRIVE_VARS = frozenset(
    {
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
    scrub = PASSTHROUGH_VARS | LEGACY_DRIVE_VARS
    env = {k: v for k, v in os.environ.items() if k not in scrub}
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


def test_environment_keys_match_the_passthrough_contract() -> None:
    """Env set-equality drift guard (T2.12): the rendered environment keys are exactly
    TZ + HERMES_UID/GID + PASSTHROUGH_VARS — no var can silently appear or vanish."""
    proc = _render_config()

    assert proc.returncode == 0, proc.stderr
    config = yaml.safe_load(proc.stdout)
    environment = config["services"]["gateway"]["environment"] or {}
    rendered = set(environment.keys())
    expected = {"TZ", "HERMES_UID", "HERMES_GID"} | set(PASSTHROUGH_VARS)
    assert rendered == expected, (
        f"unexpected env keys: {sorted(rendered - expected)}; "
        f"missing: {sorted(expected - rendered)}"
    )


def test_deleted_sync_script_is_not_mounted() -> None:
    """T2.28 deletion guard: scripts/sync_knowledge.py is gone (proposal §12) and no
    volume may reference it anymore."""
    proc = _render_config()

    assert proc.returncode == 0, f"compose config failed:\n{proc.stderr}"
    config = yaml.safe_load(proc.stdout)
    volumes = config["services"]["gateway"]["volumes"] or []
    leaks = [v for v in volumes if "sync_knowledge" in str(v.get("source", ""))]
    assert not leaks, f"sync machinery mount still present: {leaks}"


def test_freshness_config_mount_is_gone() -> None:
    """T2.28 deletion guard: /opt/data/config existed only for the T2.23 freshness TTL
    knob (knowledge.freshness_ttl_minutes); with the freshness gate deleted the mount
    goes too."""
    proc = _render_config()

    assert proc.returncode == 0, f"compose config failed:\n{proc.stderr}"
    config = yaml.safe_load(proc.stdout)
    volumes = config["services"]["gateway"]["volumes"] or []
    leaks = [v for v in volumes if str(v.get("target", "")) == "/opt/data/config"]
    assert not leaks, f"freshness config mount still present: {leaks}"


def test_hermes_ref_appears_in_rendered_config() -> None:
    """The docker/HERMES_REF pin appears verbatim in the rendered config output."""
    ref = HERMES_REF_FILE.read_text(encoding="utf-8").strip()
    proc = _render_config()

    assert proc.returncode == 0, f"compose config failed:\n{proc.stderr}"
    assert ref, f"{HERMES_REF_FILE} is empty"
    assert ref in proc.stdout, f"pinned ref {ref!r} not in rendered config"


# --- T2.30: the static site file-server (caddy) ----------------------------------------


def test_caddy_service_serves_the_site_read_only_on_loopback() -> None:
    """T2.30: a caddy file-server mounts data/site read-only and binds 127.0.0.1:8080.

    The site is rendered from the Pi-local record; exposure beyond loopback happens
    only through the cloudflared tunnel (T2.31) — never a published port.
    """
    proc = _render_config()

    assert proc.returncode == 0, f"compose config failed:\n{proc.stderr}"
    config = yaml.safe_load(proc.stdout)
    caddy = (config.get("services") or {}).get("caddy")
    assert caddy is not None, "caddy service missing"

    volumes = caddy.get("volumes") or []
    site_mounts = [
        v
        for v in volumes
        if str(v.get("target", "")) == "/usr/share/caddy"
        and v.get("read_only") is True
        and str(v.get("source", "")).endswith("/data/site")
    ]
    assert site_mounts, f"read-only data/site mount missing; rendered volumes: {volumes}"

    ports = caddy.get("ports") or []
    loopback = [
        p
        for p in ports
        if str(p.get("host_ip", "")) == "127.0.0.1"
        and str(p.get("published", "")) == "8080"
        and str(p.get("target", "")) == "80"
    ]
    assert loopback, f"caddy must bind 127.0.0.1:8080->80 only; rendered ports: {ports}"
    assert len(ports) == 1, f"caddy binds unexpected extra ports: {ports}"


def test_gateway_service_publishes_no_ports() -> None:
    """T2.30 guard: the gateway stays host-networked with no published ports — the
    'no inbound ports' property holds as services are added (proposal §5)."""
    proc = _render_config()

    assert proc.returncode == 0, f"compose config failed:\n{proc.stderr}"
    gateway = yaml.safe_load(proc.stdout)["services"]["gateway"]
    assert not gateway.get("ports"), f"gateway publishes ports: {gateway.get('ports')}"
