# docker/ — hermes-agent build pin

This directory holds the one file that pins which upstream hermes-agent commit
our container is built from, plus this documentation. The compose template
(`docker-compose.yml` at the repo root) builds NousResearch/hermes-agent **from
source** — upstream publishes no image for this — at the commit recorded in
[`HERMES_REF`](HERMES_REF).

## The pin: `docker/HERMES_REF` + the compose build URL

`HERMES_REF` is a single line containing one git ref (commit SHA preferred —
tags move) of <https://github.com/NousResearch/hermes-agent>.

**The ref lives in TWO places and they must move together:**

1. `docker/HERMES_REF` — the machine-readable pin (asserted by
   `tests/test_compose.py`, which fails the suite if the pin file's contents do
   not appear verbatim in the rendered `docker compose config` output —
   the drift guard);
2. the `build:` URL in `docker-compose.yml`
   (`https://github.com/NousResearch/hermes-agent.git#<REF>` — compose builds a
   git checkout at that ref; the ref is the URL fragment, not a Dockerfile ARG,
   because the upstream Dockerfile takes no ref argument).

### Update procedure (deliberate upgrade — proposal §6.6)

1. Pick the target ref (latest release tag from the
   [releases page](https://github.com/NousResearch/hermes-agent/releases), then
   resolve it to its commit SHA; or a specific main SHA).
2. Edit **both** `docker/HERMES_REF` and the `build:` URL in
   `docker-compose.yml` to the same SHA.
3. Run `make check` (the drift-guard test must pass), commit the two files
   together.
4. On the Pi: `docker compose build && docker compose up -d`, then verify the
   bot comes back (Telegram DM) before moving on.

**Current pin:** `29112bef099274229cadff79cdff7bf7b99c4b77` — the commit that
release tag `v2026.8.31` (Hermes v0.21.0) points to, recorded 2026-09-02 (verified via
the GitHub API: refs/tags → tag object → commit). Prior pin: `5fc308a7…` = `v2026.8.27`
(v0.20.6), the commit the phase-1 gate ran against.

## The Pi build (slow, one-time)

First `docker compose up -d` on the Raspberry Pi 5 (ARM64) compiles the image
from source: the upstream multi-stage Dockerfile builds SQLite with FTS5 from
scratch, then installs the Python (uv/py3.13) and Node dependencies. Expect a
**long one-time build** — tens of minutes to a couple of hours on the Pi — and
plan the initial deployment accordingly (8 GB Pi 5 is fine; proposal §5).
Later `up`s reuse the build cache; a ref bump rebuilds only changed layers.

## Mounts

| Host path | Container path | Purpose |
|---|---|---|
| `~/.hermes` | `/opt/data` | Hermes home: `config.yaml`, `SOUL.md` (installed by `scripts/setup.sh`), profiles, sessions, cron state. Survives image updates. |
| `./src/coordinator` | `/opt/data/plugins/coordinator` (read-only) | Our coordinator plugin, mounted into the user-plugin directory (upstream: user plugins live under `<HERMES_HOME>/plugins/`). |
| `./data` | `/opt/data/workspace` | Our runtime state: `data/hermes/hermes-coord.db` (members/checkins/settings + knowledge cache) and `data/project/` (the agent workspace — AGENTS.md, journal/, inbox/; authored files are uploaded to Drive via $GAPI, not mirrored). Gitignored; never commit. |

`docker compose config` is valid while `data/` does not exist yet — bind mounts
are only materialized at `up`.

Note: upstream's compose also defines a localhost-only `dashboard` service;
this template intentionally ships the `gateway` service only (all interaction
is via Telegram).

## Environment & secrets flow

- **`TZ=UTC`** — fixed in the compose file (timezone law, AGENTS.md #4): Hermes
  anchors every cron schedule to this single zone; per-member conversions
  happen inside the coordinator plugin.
- **`HERMES_UID` / `HERMES_GID`** — passthrough (`${HERMES_UID:-10000}`); the
  upstream s6 hook remaps the container's `hermes` user so bind-mount files
  stay owned by the host user. On the Pi, set both in `.env` to your own
  `id -u` / `id -g`.
- **Secrets** — `TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY` (and the
  `TELEGRAM_*` scoping vars) are declared as bare list-form environment
  entries. **No values are in this repo**: fill `.env` from `.env.example`
  (never commit it), then make the values visible to compose at runtime:

  ```sh
  set -a; source .env; set +a
  docker compose up -d
  ```

  Unset entries render as `null` — `docker compose config` exits 0 with **no**
  `.env` present, which is what CI asserts.
