# finki-coordinator

A clonable template that boots a **Telegram-based async project coordinator** on a
Raspberry Pi 5: a Python coordinator plugin (SQLite-backed members, check-ins, and
settings with UTC-anchored, timezone-safe cron scheduling), a Pi-local knowledge base
(`data/project/docs/` — git-versioned, rendered to a static site) with Google Drive
demoted to daily backup + document inbox through Hermes' built-in google-workspace
skill ($GAPI), and an OpenExecutive-derived operator persona.

> 🚧 Under active development — Phase 0 (foundation), Phase 1 (core bot), Phase 2
> (member lifecycle), and the v7 knowledge restructure (Phase 2.5: the Pi is the
> record; Drive = backup + inbox; static site + Cloudflare Tunnel) are implemented;
> the Phase-2.5 manual gate is the current milestone. Persona (Phase 3) and
> hardening (Phase 4) land next ([ROADMAP.md](ROADMAP.md)).

## What you get

- **8 coordinator tools** in every bot session: `member_add`, `member_update`,
  `member_list`, `member_delete` (owner-only), `checkin_submit`, `checkins_by_date`,
  `setting_get`, `setting_set`.
- **Timezone-safe scheduling** — each member's local wake time becomes a UTC cron entry
  (08:00 in Quito → `0 13 * * *`), computed in pure Python (`src/coordinator/scheduling.py`),
  never by the LLM. DST is resolved at schedule-computation time from an explicit instant.
- **The relay law** — the bot never recomputes schedules. Handlers return a pre-computed
  `cron_relay` payload that the agent relays verbatim to Hermes' cron.
- **Drift guards** — `cron.model` pinned globally by `setup.sh` so every scheduled job
  inherits the pin (the drift guard), `TELEGRAM_ALLOWED_USERS` as a static DM gate, model
  choice reserved to the owner.
- **Runtime `AGENTS.md`** — generated from the roster DB and injected into every session;
  its query map: mission → `docs/product/brief.md` first, project questions → search
  the `docs/` files directly, status → `journal/` + `checkins_by_date`,
  tasks → the `kanban_*` tools.
- **Check-in, digest, inbox & backup skills**, an operator persona
  (`prompts/persona.md` → `SOUL.md`), and a kanban board driven through Hermes'
  built-in tools.
- **Static site + tunnel** — `make site-build` renders the record with MkDocs
  Material (arm64 docker); caddy serves it on host loopback :8080; a host crontab
  line (`*/15`, installed by `setup.sh`) keeps it fresh — dumb and LLM-free.
  One outbound-only `cloudflared` tunnel exposes site + dashboard behind
  Cloudflare Access (Google SSO) — no inbound ports, ever.
- **Pi-local knowledge base (v7)** — the record is `data/project/docs/` on the Pi,
  its own git repo (the safety net), rendered by MkDocs Material to a static site.
  Google Drive is backup + inbox only: a daily 03:00 UTC agent job pushes `docs/` to
  Drive `knowledge_base/`, and humans drop documents into Drive `input/` for the
  agent to ingest (read → synthesize → write `docs/` → move to `processed/`).
  No rclone, no mirror, no sync script, no FTS5 cache.

## How it fits together

```
Telegram ⇄ hermes-agent gateway (Docker, network_mode: host — long-poll, no inbound ports)
              │  static allow-list gate: TELEGRAM_ALLOWED_USERS
              ├─ coordinator plugin (this repo, src/coordinator — mounted read-only)
              │     └─ SQLite: data/hermes/hermes-coord.db (members / checkins / settings)
              ├─ cron: UTC-anchored jobs, cron.model pinned (jobs inherit)
              ├─ data/project/docs/ (the record, git) ─ make site-build ─► data/site/
              │        └─ caddy (127.0.0.1:8080, read-only) ─┐
              ├─ Hermes dashboard (/kanban, 127.0.0.1:9119) ─┤
              │                                              ▼
              │                              cloudflared (outbound-only tunnel)
              │                                              ▼
              │                    Cloudflare edge — Access: Google SSO (team only)
              │                                              ▼
              │                          kb.<domain> · board.<domain>
              └─ Drive via $GAPI: input/ (humans drop) · processed/ (ingested) ·
                 knowledge_base/ (daily 03:00 UTC backup of docs/)
```

The compose file builds
[hermes-agent](https://github.com/NousResearch/hermes-agent) from source at the commit
pinned in [`docker/HERMES_REF`](docker/HERMES_REF) (drift-guarded by tests). The whole
container runs `TZ=UTC`; per-member timezone math happens only inside the coordinator
plugin, never per job.

## Requirements

- Docker + Compose. The reference target is a Raspberry Pi 5 (8 GB, 64-bit OS; an SSD is
  strongly recommended — an SD-card install works but trades away durability). The first
  ARM64 build compiles from source and takes 20–60 min (one-time, cached after).
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)) + your numeric user ID.
- An [OpenRouter](https://openrouter.ai/keys) API key **with a per-key credit limit** —
  that limit is the money valve.
- A dedicated Google account for the Drive backup + inbox — the agent connects
  in-container via Hermes' built-in google-workspace skill ($GAPI); no local OAuth files.
- A Cloudflare account with a domain you control — the site + dashboard are exposed
  through a remotely-managed tunnel behind Cloudflare Access (owner-manual, ~15 min).
- [`uv`](https://docs.astral.sh/uv/) — runs the host-side setup scripts and the dev suite.

## Quickstart

```sh
git clone https://github.com/juan-ee/finki-coordinator.git
cd finki-coordinator
```

1. **Secrets** — `cp .env.example .env` and fill it: `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_ALLOWED_USERS` (comma-separated numeric Telegram IDs), `OPENROUTER_API_KEY`.
   On the Pi set `HERMES_UID` /
   `HERMES_GID` to your `id -u` / `id -g`. Never commit `.env`. To onboard a member
   later, run `scripts/allow.sh <id>...` — it appends their ID and applies with
   `docker compose up -d` (never edit the gate by hand and restart; proposal §8.2).
2. **Config** — `cp config/config.example.yaml config/config.yaml`, then set the project
   name, the Drive backup + inbox root, and the anchor timezone for human-facing times
   (digest).
3. **Roster** — edit `config/members.seed.yaml` (names, IANA timezones, local wake
   times). It seeds the database exactly once, then the DB is authoritative. Keep
   `telegram_id` values null — real Telegram IDs are never committed (v6 seed
   hygiene); they enter via `scripts/allow.sh <id>...` (the gitignored `.env` door)
   and the door-first onboarding flow (proposal §8.2). A pre-known founding roster
   with real IDs belongs in a gitignored local seed file passed via `--seed`.
4. **Setup** — `uv sync`, then `uv run ./scripts/setup.sh`: validates the config,
   installs `prompts/persona.md` → `~/.hermes/SOUL.md` and every skill, applies the
   Hermes config (model + `cron.model` pin + `timezone UTC`), seeds
   `data/project/` from `project-template/` and **git-inits `data/project/docs/`**
   (the record's local history), installs the `*/15` site-rebuild crontab line, and
   prints the 03:00 UTC knowledge-backup cron command (idempotent; re-runs never
   overwrite your content). If the `hermes` CLI is absent on the host it prints the
   equivalent in-container commands — run them after first boot.
5. **Database** — `uv run python scripts/init_db.py --db data/hermes/hermes-coord.db
   --seed config/members.seed.yaml`. Idempotent: a re-run prints the `seeded_at` skip
   note and exits 0.
6. **Boot** — `set -a; source .env; set +a; docker compose up -d` (first start builds —
   be patient), then DM the bot from an allow-listed account. Build the site once:
   `make site-build`.
7. **Expose (owner-manual)** — follow the click-path in
   [`docker/README.md`](docker/README.md): create the Cloudflare tunnel, paste its
   token into `.env` as `CLOUDFLARE_TUNNEL_TOKEN`, add the public hostnames
   (`kb.*` → caddy, `board.*` → dashboard), and gate both with an Access policy
   (Google SSO, team only).
8. **Verify** — work through [`docs/verify/phase1.md`](docs/verify/phase1.md) (plugin
   tools visible, kanban reachable, the timezone matrix, the `cron.model` pin,
   runtime-`AGENTS.md` injection) and
   [`docs/verify/phase2.5.md`](docs/verify/phase2.5.md) (site behind the tunnel,
   Access blocking non-team accounts, `/kanban` drag-drop, the 03:00 backup landing
   in Drive, the inbox end-to-end, the restore drill).

## Development

```sh
make install    # uv sync (creates .venv from the committed uv.lock)
make test       # pytest — unit + integration; deterministic, no network, no Docker
make lint       # ruff format --check + ruff check
make type       # mypy --strict src/coordinator
make check      # all of the above — must pass before every commit
make site-build # render the record to data/site (docker mkdocs-material)
```

The engineering constitution lives in [AGENTS.md](AGENTS.md), architecture and decisions
in [proposal.md](proposal.md), the atomic work queue in [ROADMAP.md](ROADMAP.md), and
per-phase verification scripts in [`docs/verify/`](docs/verify/).

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundation (package, schema, scheduling, compose) | ✅ done |
| 1 | Core bot (persona, skills, integration day-flow, Pi gate) | ✅ gate signed off |
| 2 | Member lifecycle (door script, tools, digest) | ✅ gate signed off |
| 2.5 | v7 knowledge restructure (Pi-local record, site + tunnel, backup + inbox) | 🚧 manual gate |
| 3 | Persona (toggle, triage rubric, third-party notices) | ⬜ |
| 4 | Hardening (backup/restore, runbooks) | ⬜ |
| 5 | Vector-RAG spike — feasibility notes only, deliberately deferred | ⬜ |

## License

MIT — see [LICENSE](LICENSE). The persona is derived from
[SenteLabsAI/OpenExecutive](https://github.com/SenteLabsAI/OpenExecutive) (Apache 2.0);
full attribution lands with the Phase-3 notices file.
