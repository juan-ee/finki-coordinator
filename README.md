# finki-coordinator

A clonable template that boots a **Telegram-based async project coordinator** on a
Raspberry Pi 5: a Python coordinator plugin (SQLite-backed members, check-ins, and
settings with UTC-anchored, timezone-safe cron scheduling), a Google Shared Drive
knowledge base managed through Hermes' built-in google-workspace skill ($GAPI) with a
local SQLite FTS5 index, and an OpenExecutive-derived operator persona.

> 🚧 Under active development — Phase 0 (foundation), Phase 1 (core bot), and the
> v6 Phase-2 restructure (member lifecycle + knowledge) are implemented; the Phase-2
> manual gate ($GAPI on the Pi) is the current milestone. Persona (Phase 3) and
> hardening (Phase 4) land next ([ROADMAP.md](ROADMAP.md)).

## What you get

- **9 coordinator tools** in every bot session: `member_add`, `member_update`,
  `member_list`, `member_delete` (owner-only), `checkin_submit`, `checkins_by_date`,
  `setting_get`, `setting_set`, `knowledge_search` (FTS5 over the cache).
- **Timezone-safe scheduling** — each member's local wake time becomes a UTC cron entry
  (08:00 in Quito → `0 13 * * *`), computed in pure Python (`src/coordinator/scheduling.py`),
  never by the LLM. DST is resolved at schedule-computation time from an explicit instant.
- **The relay law** — the bot never recomputes schedules. Handlers return a pre-computed
  `cron_relay` payload that the agent relays verbatim to Hermes' cron.
- **Drift guards** — `cron.model` pinned globally by `setup.sh` so every scheduled job
  inherits the pin (the drift guard), `TELEGRAM_ALLOWED_USERS` as a static DM gate, model
  choice reserved to the owner.
- **Runtime `AGENTS.md`** — generated from the roster DB and injected into every session;
  its query map: mission → the Drive brief first, project questions → `knowledge_search`
  (then confirm against the live Drive file), status → `journal/` + `checkins_by_date`,
  tasks → the `kanban_*` tools.
- **Check-in & digest skills**, an operator persona (`prompts/persona.md` → `SOUL.md`),
  and a kanban board driven through Hermes' built-in tools.
- **Google Shared Drive knowledge base (v6.1)** — the agent is the team's librarian:
  a deterministic sync script (`scripts/sync_knowledge.py` — `make sync` + a nightly
  `hermes cron` job, no LLM in the data path) incrementally caches Drive documents
  into a local FTS5 index, `knowledge_search` finds the right document, and
  agent-authored journals are uploaded back via `$GAPI drive upload`. No rclone, no
  mirror, no host crontab.

## How it fits together

```
Telegram ⇄ hermes-agent gateway (Docker, network_mode: host — long-poll, no inbound ports)
              │  static allow-list gate: TELEGRAM_ALLOWED_USERS
              ├─ coordinator plugin (this repo, src/coordinator — mounted read-only)
              │     └─ SQLite: data/hermes/hermes-coord.db (members / checkins / settings)
              ├─ cron: UTC-anchored jobs, cron.model pinned (jobs inherit)
              └─ Google Drive ⇄ $GAPI (sync script / knowledge_search / upload)
                    local FTS5 cache: hermes-coord.db · data/project/ = agent workspace
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
- A Google account that can reach the team's Shared Drive folder — the agent connects
  in-container via Hermes' built-in google-workspace skill ($GAPI); no local OAuth files.
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
   name, Shared Drive folder, and the anchor timezone for human-facing times (digest).
3. **Roster** — edit `config/members.seed.yaml` (names, IANA timezones, local wake
   times). It seeds the database exactly once, then the DB is authoritative. Keep
   `telegram_id` values null — real Telegram IDs are never committed (v6 seed
   hygiene); they enter via `scripts/allow.sh <id>...` (the gitignored `.env` door)
   and the door-first onboarding flow (proposal §8.2). A pre-known founding roster
   with real IDs belongs in a gitignored local seed file passed via `--seed`.
4. **Setup** — `uv sync`, then `uv run ./scripts/setup.sh`: validates the config,
   installs `prompts/persona.md` →
   `~/.hermes/SOUL.md`, and applies the Hermes config (model + `cron.model` pin +
   `timezone UTC`). If the `hermes` CLI is absent on the host it prints the
   `hermes config set …` commands — run them inside the container after first boot, e.g.
   `docker compose exec gateway hermes config set cron.model <model>`.
5. **Database** — `uv run python scripts/init_db.py --db data/hermes/hermes-coord.db
   --seed config/members.seed.yaml`. Idempotent: a re-run prints the `seeded_at` skip
   note and exits 0.
6. **Boot** — `set -a; source .env; set +a; docker compose up -d` (first start builds —
   be patient), then DM the bot from an allow-listed account.
7. **Verify** — work through [`docs/verify/phase1.md`](docs/verify/phase1.md), the
   human-only gate: plugin tools visible, kanban reachable, the fake-member timezone
   matrix, the `cron.model` pin, and runtime-`AGENTS.md` injection.

## Development

```sh
make install    # uv sync (creates .venv from the committed uv.lock)
make test       # pytest — unit + integration; deterministic, no network, no Docker
make lint       # ruff format --check + ruff check
make type       # mypy --strict src/coordinator
make check      # all of the above — must pass before every commit
```

The engineering constitution lives in [AGENTS.md](AGENTS.md), architecture and decisions
in [proposal.md](proposal.md), the atomic work queue in [ROADMAP.md](ROADMAP.md), and
per-phase verification scripts in [`docs/verify/`](docs/verify/).

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundation (package, schema, scheduling, compose) | ✅ done |
| 1 | Core bot (persona, skills, integration day-flow, Pi gate) | ✅ gate signed off |
| 2 | Member lifecycle & knowledge (v6 — door script, tools, Drive loop) | 🚧 manual gate |
| 3 | Persona (toggle, triage rubric, third-party notices) | ⬜ |
| 4 | Hardening (backup/restore, runbooks) | ⬜ |
| 5 | Vector-RAG spike — feasibility notes only, deliberately deferred | ⬜ |

## License

MIT — see [LICENSE](LICENSE). The persona is derived from
[SenteLabsAI/OpenExecutive](https://github.com/SenteLabsAI/OpenExecutive) (Apache 2.0);
full attribution lands with the Phase-3 notices file.
