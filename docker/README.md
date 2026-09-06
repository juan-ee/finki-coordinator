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
| `./data` | `/opt/data/workspace` | Our runtime state: `data/hermes/hermes-coord.db` (members/checkins/settings) and `data/project/` (the agent workspace — AGENTS.md, `docs/` = the knowledge record, journal/, inbox/). Gitignored; never commit. |

## Scheduled jobs

v7 (T2.28): the v6.1 nightly knowledge-sync job is deleted along with the sync
machinery (proposal §12) — the knowledge record lives in `data/project/docs/` and
needs no sync. The v7-era scheduled job is the **daily 03:00 UTC Drive backup**
(agent cron, T2.32): it commits the docs repo locally, then uploads it to the Drive
`knowledge_base/` folder (git commit BEFORE upload; failure posts loudly).

`docker compose config` is valid while `data/` does not exist yet — bind mounts
are only materialized at `up`.

## The static knowledge site (v7, T2.30)

The record (`data/project/docs/`) renders to a static site: `make site-build` runs
[`squidfunk/mkdocs-material`](https://hub.docker.com/r/squidfunk/mkdocs-material)
(arm64) in docker against the committed `mkdocs.yml` and writes `data/site/`. The
compose `caddy` service serves `data/site/` **read-only** on host loopback
`127.0.0.1:8080` — nothing is published beyond the Pi. The rebuild path is dumb and
LLM-free (rule-11 spirit): a host crontab line (installed idempotently by
`setup.sh` step 9/9, every 15 min UTC) runs `make site-build`; run it by hand any
time for an on-demand rebuild. First use: `docker pull squidfunk/mkdocs-material`
(or let the first build pull it).

> The record itself (`data/project/docs/`) is the agent's to write — the site is a
> pure render of it. Never edit `data/site/` by hand: the next rebuild overwrites it.

## The Cloudflare Tunnel + Access (v7, T2.31)

One outbound-only `cloudflared` service exposes the site **and** the Hermes
dashboard through a remotely-managed tunnel behind Cloudflare Access (Google SSO,
team only). **No inbound ports** stays true: cloudflared dials Cloudflare's edge;
nothing listens on the internet. The dashboard's fail-closed basic-auth gate
(`HERMES_DASHBOARD=true` + the `HERMES_DASHBOARD_BASIC_AUTH_*` pair in `.env`)
remains the second layer behind Access.

**Owner click-path (manual, after the phase — no placeholder credentials ship in
this repo):**

1. **Dashboard on:** set `HERMES_DASHBOARD=true` and a strong basic-auth
   username/password pair in `.env` (fail-closed: without the pair the dashboard
   does not open).
2. **Create the tunnel:** Cloudflare dashboard → **Zero Trust → Networks →
   Tunnels → Create a tunnel** → type *Cloudflared* → name it (e.g.
   `hermes-pi`) → copy the **token** into `.env` as `CLOUDFLARE_TUNNEL_TOKEN=...`
   → install the connector by just bringing the stack up (`docker compose up -d`
   runs our `cloudflared` service with `tunnel run`; do NOT run the dashboard's
   copy-paste `docker run` line).
3. **Public hostnames** (in the same tunnel's **Public Hostname** tab — configured
   in the Cloudflare dashboard, never in files):
   - `kb.<your-domain>` → service `HTTP://caddy:80` (the static site),
   - `board.<your-domain>` → service `HTTP://host.docker.internal:9119` (the
     Hermes dashboard; the `host-gateway` alias in compose makes the host's
     loopback dashboard reachable from the cloudflared container).
4. **Access policies:** Zero Trust → **Access → Applications → Add an application
   → Self-hosted** for each hostname → identity provider **Google SSO** (team
   accounts only; restrict by email list). A non-team Google account must be
   rejected at the edge (phase-2.5 gate item 2).
5. **Verify:** from a non-Pi device, `https://kb.<domain>` renders the site behind
   the Google login; `https://board.<domain>` shows the dashboard (`/kanban` tab)
   behind Access + basic auth; the Pi itself still works with zero inbound ports
   (`docker compose ps` shows no published ports on gateway/cloudflared).

Ecuador latency note (proposal §12): ~250 ms/click from Guayaquil is expected;
static pages are edge-cached, dashboard actions are not.

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
