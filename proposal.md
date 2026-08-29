# Hermes — Async Project Coordinator Template (v5)

**Goal:** a *clonable template repo* that anyone can copy, fill two config files, and run on
their own hardware: an always-on Telegram coordinator with a Google Shared Drive as the
knowledge source — and **no restart needed for everyday changes**.

**Confirmed decisions (rounds 1–4):**
- Pi **5, 8 GB RAM**, 64-bit OS on SSD, 40 GB free · Docker Compose, built from source at a
  **pinned `HERMES_REF`** (no published image exists upstream).
- Model via **OpenRouter** (Hermes 4 70B, `nousresearch/hermes-4-70b`) · **$20/month cap
  enforced as an OpenRouter per-key limit, set once in the console** (README checklist;
  re-apply whenever the key is rotated) · DMs allowed · dedicated Drive account.
- Members → **SQLite DB, queried live by agent tools**. Kanban (built-in) handles tasks.
- **Two-class configuration:** anything the team can *say out loud* (digest time/chat, nudge
  limit, member wake times, cron schedules) lives in SQLite and is agent-editable live —
  no restart, no SSH. Anything that is a **money or infrastructure valve** (model id,
  group id, drive root, RAG toggle) lives in `config.yaml` and stays operator-owned.
  The agent is the only day-to-day interface; nobody on the team edits files to change behavior.
- **No helper processes.** No reconciler script, no daemons: the plugin computes, the agent
  relays, the daily digest makes drift visible.
- Secrets in `.env` (gitignored). Template follows best practices.

---

## 1. Runtime model: config for boot, DB for everything live

**Principle:** *boot defaults* in `config.yaml` (validated, changes need a restart) vs
*live state* in SQLite (changes take effect on the next tool call — no restart).

```
             ┌─────────────────────────────────────────────────────┐
             │  data/hermes/hermes-coord.db  (SQLite, live state)  │
             │   members · checkins · settings — read/written by   │
             │   the coordinator plugin tools on every turn        │
             └──────────────┬──────────────────────────────────────┘
                            │ tools: member_add/update/list, checkin_submit,
                            │ checkins_by_date, setting_get/set
               Telegram     │   (agent calls them; humans talk to the bot)
     "add Leo, Europe/Berlin, wake 10:00"  ─────────►  INSERT member
                            │        + tool returns a ready-made cronjob call
                            │        + agent relays it → job `checkin-4` live
```

- **Adding/updating a member** = one Telegram message. `member_add`/`member_update` write
  the DB, then **return a ready-made `cronjob` call**: the UTC schedule is computed with
  Python `zoneinfo` *inside the plugin* — the LLM never does timezone arithmetic, it relays
  the call verbatim to the built-in `cronjob` tool (`create`/`edit` by stable name
  `checkin-<member_id>`; name-based lookup is supported).
  Effect is live from the next cron tick — **no restart**. Members change their own wake
  time by simply asking the bot.
- **Timezone design (UTC anchor):** the container and Hermes' native `timezone` config stay
  **UTC**. `wake` is stored in the member's local time; conversion happens only in plugin code.
  - *Ecuador* (`America/Guayaquil`, UTC-5, no DST): jobs are created once and never change.
  - *Germany* (`Europe/Berlin`): shifts twice a year → one conversational command
    ("recalculate all check-in schedules") re-upserts every job with fresh conversions.
    Put a phone reminder on it: Hermes **disables cron-management tools inside cron runs**,
    so DST self-healing must be asked for interactively. EU transitions land at 01:00 UTC
    at night — recomputing the next morning loses nobody any sleep.
  - ([Cron internals](https://hermes-agent.nousresearch.com/docs/developer-guide/cron-internals))
- **Settings** (`digest_time`, `digest_chat`, `nudge_limit`) live **only** in the `settings`
  key-value table; the plugin ships hardcoded defaults for missing keys — **no precedence
  rules**. Changing `digest_time` works like a wake time: the tool returns the fresh cron
  expression, the agent edits the digest job (`digest`).
- **Drift has no script — by design.** If a schedule update ever goes wrong, the symptom is
  a missed check-in, which the daily digest surfaces automatically. For a deliberate audit,
  ask the agent: *"compare members against cron jobs and fix mismatches."* The reconciler
  is a prompt, not a process.
- **Persona** lives in `SOUL.md` (Hermes' always-loaded identity slot, read from
  `HERMES_HOME`); the repo ships it as `prompts/persona.md` and `setup.sh` installs it.
  Skills load per use (Hermes' self-improvement loop even writes new skills at runtime).
  AGENTS.md is injected at **session start** — regenerating it reaches the next session,
  not the next tool call. Only true boot defaults (model id, drive root, group id) need a
  restart, and those change rarely.

**Two databases, one job each (no duplication):**
| DB | Owns | Written by |
|---|---|---|
| `~/.hermes/kanban.db` (Hermes built-in) | tasks: create/list/complete/block/review | `kanban_*` tools |
| `data/hermes/hermes-coord.db` (ours) | members, checkins, settings | coordinator plugin tools |

**The coordinator plugin** is a small Python Hermes toolset (`register_tool` — the
documented plugin path; stdlib `sqlite3` + `zoneinfo`, zero extra dependencies) exposing
**7 tools**:
`member_add` · `member_update` · `member_list` · `checkin_submit` · `checkins_by_date` ·
`setting_get` · `setting_set`.
(Cuts vs v4: `member_deactivate` → `member_update` with `active=0`; `member_get` →
`member_list` filtered; `board_snapshot` → the `kanban_*` toolset already covers it.
Every tool is prompt tokens and misuse surface.)
Every DB connection opens with `PRAGMA journal_mode=WAL` + `busy_timeout=5000` — the
gateway, the journal export and any script share the file without lock errors.

### SQLite schema (hermes-coord.db)

```sql
CREATE TABLE members (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  telegram_id INTEGER UNIQUE,
  timezone    TEXT NOT NULL DEFAULT 'UTC',   -- IANA name, e.g. 'America/Guayaquil'
  wake        TEXT,                          -- 'HH:MM' in member's local time
  role        TEXT,
  status_days TEXT,                          -- JSON: ["mon","wed","fri"] (empty = every day)
  active      INTEGER DEFAULT 1,
  created_at  TEXT, updated_at TEXT
);
CREATE TABLE checkins (
  id         INTEGER PRIMARY KEY,
  member_id  INTEGER REFERENCES members(id),
  date       TEXT NOT NULL,                 -- 'YYYY-MM-DD'
  done       TEXT, next TEXT, blockers TEXT,
  source     TEXT DEFAULT 'auto',           -- auto | manual
  created_at TEXT,
  UNIQUE(member_id, date)                  -- latest wins: one check-in per member per day
);
CREATE TABLE settings (                    -- runtime knobs (key → value, TEXT)
  key   TEXT PRIMARY KEY,                  -- digest_time, digest_chat, nudge_limit
  value TEXT
);
```

Bootstrap: `scripts/init_db.py` seeds from `config/members.seed.yaml` **once** on first
boot, then the DB is authoritative; the seed file is documentation only.

---

## 2. Config & secrets — what is committed (explicit)

| Path | Committed? | Purpose |
|---|---|---|
| `.env.example` | ✅ **yes** | template of required secrets (what you copy) |
| `.env` | ❌ **no** (gitignored) | real secrets — never committed |
| `config/config.example.yaml` | ✅ yes | the documented schema (copy me) |
| `config/config.yaml` | ❌ no (gitignored) | real boot defaults |
| `config/config.schema.json` | ✅ yes | JSON Schema; validated by `setup.sh` + CI |
| `config/members.seed.yaml` | ✅ yes | bootstrap seed example (imported once, then DB wins) |
| `prompts/`, `scripts/`, `docker-compose.yml`, `README.md` | ✅ yes | template code |
| `THIRD_PARTY_NOTICES.md` | ✅ yes | Apache 2.0 attribution for extracted OpenExecutive content |
| `data/**` | ❌ no (gitignored) | runtime state: kanban.db, hermes-coord.db, Drive mirror |

### `.env.example` — the **complete** secret inventory (all envs)

```dotenv
# ── Telegram ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=                # from @BotFather (bot token)
TELEGRAM_ALLOWED_USERS=            # comma-separated user IDs allowed to DM/trigger the bot
                                   # (static gate: one line + gateway restart per new member)
# TELEGRAM_GROUP_ALLOWED_CHATS=    # optional: team group chat id the bot may interact with
# TELEGRAM_GROUP_ALLOWED_USERS=    # optional: group-only senders (no DM access)

# ── Model API (OpenRouter) ─────────────────────────────────
OPENROUTER_API_KEY=                # https://openrouter.ai/keys
                                   # → set a $20 monthly credit limit on this key
                                   #   in the OpenRouter console (README step 4)

# ── Google Drive (rclone via OAuth) ────────────────────────
# Setup: Google Cloud console → create OAuth client (Desktop app) →
#        enable Drive API → paste ID+Secret; then run
#        `rclone config` once and copy the refresh token below.
GOOGLE_DRIVE_CLIENT_ID=            # OAuth client id (per-app credential)
GOOGLE_DRIVE_CLIENT_SECRET=        # OAuth client secret
GOOGLE_DRIVE_REFRESH_TOKEN=        # authorized session (from rclone token JSON)
RCLONE_REMOTE=shareddrive:         # rclone remote name (referenced by compose)
RCLONE_ROOT_FOLDER_ID=             # optional: pin sync to a specific Drive folder id

# ── ALTERNATIVE headless auth (service account, no OAuth dance) ──
# GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON=/run/secrets/gsa.json   # or base64 string
```

`setup.sh` consumes these and writes `/root/.config/rclone/rclone.conf`
(client_id, client_secret, token, root_folder_id) — the container only sees the
remote name. **Full env inventory (secrets vs config):**

| Env var | Secret? | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram gateway |
| `TELEGRAM_ALLOWED_USERS` | — | who may DM/trigger the bot ([Hermes allowlist](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram); filled once for the founding team) |
| `TELEGRAM_GROUP_ALLOWED_CHATS` | — | authorized team group chat id (digest delivery target) |
| `TELEGRAM_GROUP_ALLOWED_USERS` (alt) | — | group-only senders, without DM access |
| `OPENROUTER_API_KEY` | ✅ | LLM calls (monthly limit set in OpenRouter console) |
| `GOOGLE_DRIVE_CLIENT_ID` | ⚠️ per-app | OAuth app identity |
| `GOOGLE_DRIVE_CLIENT_SECRET` | ✅ | OAuth app secret |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | ✅ | authorized Drive session |
| `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` (alt) | ✅ | headless auth path (add the SA as a Shared Drive member) |
| `RCLONE_REMOTE` | — | remote name |
| `RCLONE_ROOT_FOLDER_ID` | — | sync scope |

### `config.yaml` — boot defaults only (validated against schema)

```yaml
project:
  name: my-project
  drive_root: "MyProject"          # folder inside the Shared Drive
  timezone: Europe/Berlin          # anchor for human-facing times (digest_time)
telegram:
  group_id: ""                     # optional; empty = no group broadcasts
model:
  provider: openrouter
  default_model: nousresearch/hermes-4-70b
  # No budget knob here: the $20 cap is the OpenRouter per-key limit (console).
rag:
  enabled: false                   # optional escalation layer (phase 5)
  chunk_size: 800
  embed_model: bge-small-en-v1.5
log_level: info
```

`setup.sh` writes the model into the Hermes configuration **twice**: as the global default
**and** as `cron.model` (the cron pin). This is deliberate: unpinned cron jobs follow the
global default and *fail closed* (silently skip) whenever the global default changes —
Hermes' drift guard. Pinning `cron.model` makes the nightly machinery immune to config
experiments. `setup.sh` also sets Hermes' native top-level `timezone: UTC`.
The model id is the one variable the agent never owns: Hermes itself blocks the agent's
`cronjob` tool from changing model pins — model choice is the money valve.

Runtime things are **not** in config.yaml because they're DB state: members, check-ins,
`digest_time`, `nudge_limit`, `digest_chat`, per-member timezone/wake, all cron schedules.

---

## 3. `project/` folder structure (the Drive mirror the agent queries)

```
data/project/                        ← rclone bisync ⇄ Shared Drive: "project/**"
├── AGENTS.md                        ← GENERATED (roster + this structure + query map)
├── README.md                        ← human onboarding; static
├── docs/                            ← long-lived knowledge
│   ├── index.md                     ← curated map of docs/ (reviewed monthly)
│   ├── product/                     ← specs, roadmap, user research
│   │   └── brief.md                 ← what we're thriving for — filled once at first boot
│   │                                   (template in templates/); the agent reads it first
│   ├── decisions/                   ← ADRs: 0001-kebab-case.md, 0002-… (numbered)
│   ├── meetings/                    ← 2026-08-28-sync.md (template in templates/)
│   └── howto/                       ← operational playbooks (dev, deploy, tooling)
├── journal/                         ← GENERATED daily digests (from checkins table)
│   └── 2026-08-28.md
├── inbox/                           ← drop zone: anything unfiled. Agent triages weekly:
│                                      → docs/**, → decisions/, or → .archive/
├── templates/                       ← brief.md · adr.md · meeting-notes.md · proposal.md
├── assets/                          ← images, diagrams, binaries
├── people/                          ← optional per-member notes (never the roster)
└── .archive/                        ← moved, never deleted (agent's curator pass)
```

**Sync (bisync, with guardrails):** the agent authors documentation as the team's PM, so
bidirectional sync is a requirement. Run bisync **every 15–30 min + at container boot** —
small diffs, rare conflicts, nearly-live mirror. Three guardrails:
1. **Never run `--resync` by hand** without reading its output — a 5-line recovery runbook
   lives in the README (blind `--resync` is the only way bisync eats files).
2. `data/project/` and both SQLite DBs are in the nightly backup (phase 4).
3. Conflicts are rare by construction: humans edit from Drive, the agent writes from the
   Pi, different files. With 4 people this holds.

**Editorial policy (independent of the plumbing):** the agent files drafts into `inbox/`;
weekly triage moves them into `docs/**` and posts a *"what I filed"* summary to the group —
humans keep editorial control with near-zero effort. `journal/`, `inbox/` and `.archive/`
are agent-writable; `docs/` placement always goes through triage.

**Query map (written into AGENTS.md):** mission & goals → `docs/product/brief.md` (read
before any major ask); questions about the project → `search_files` under
`docs/` (product/decisions/meetings/howto); status/activity → `journal/` by date or
`checkins_by_date` tool; tasks → `kanban_*` tools (never files); "what's new" → `inbox/`
triage; who/availability → `member_list` (never a file). Templates live in `templates/`.

**Loading:** the gateway runs with `data/project` as working directory, so `AGENTS.md` is
injected into DM sessions automatically (Hermes discovers context files from the session
CWD). Cron jobs that need it pass `workdir=` (those then run sequentially — fine at this
scale).

---

## 4. OpenExecutive prompt selection (Apache 2.0; attribution in THIRD_PARTY_NOTICES.md)

Take: `executive_persona.py` (rewritten as group-PM persona → installed as `SOUL.md` —
behavior only), `triage_prompt.py` (severity + routing + privacy invariant).
**Defer** (add when the team feels the need): `QUALITY_REVIEWER_SYSTEM` (optional quality
gate) and the six skills — `quarterly-okr-set`, `post-mortem`, `feature-prioritization`,
`exec-1on1`→check-in, `customer-interview-plan`, `role-scorecard` (they are markdown
knowledge docs that need adapting to Hermes' skill format; none is needed for the core loop).
Skip: all **8** corporate specialist personas (strategy, finance, HR/talent, legal,
marketing, operations, product, board comms), board/fundraise/layoff skills, whole codebase
(borrow: episodic-memory→journal idea, single-claim scheduler idea).

**Hermes built-ins relied on (verified):** Telegram gateway (DMs, groups, allowlists) ·
built-in cron + `cronjob` tool (create/edit/pause by name; **model pins are user-owned** —
the agent cannot change them) · built-in Kanban + `kanban_*` toolset
(`~/.hermes/kanban.db`, shared across profiles) · document extraction (PDF/docx/xlsx) ·
agentic `search_files`/`read_file` · `AGENTS.md`/`SOUL.md` context files ·
MEMORY.md/USER.md · optional LLM-Wiki skill.
**No built-in vector RAG** — optional sqlite-vec plugin deferred to phase 5.

---

## 5. Phases

| Phase | Deliverable |
|---|---|
| 0. Foundation | Repo skeleton: compose building hermes-agent at pinned `HERMES_REF`; `.env.example`; config schema + validation; `setup.sh` (rclone conf, SOUL.md install, Hermes config: model + `cron.model` + `timezone: UTC`); `init_db`; README checklist incl. OpenRouter key limit; Pi prepared |
| 1. Core bot | Hermes + Telegram + kanban wired; coordinator plugin (7 tools); daily check-in crons + digest job. **Gates: ARM64 build · kanban + plugin live · timezone matrix (Guayaquil + Berlin, incl. a DST edge) · `cron.model` pin verified · AGENTS.md/SOUL.md wiring** |
| 2. Knowledge | rclone bisync + guardrails (cadence, recovery runbook, backup list); `AGENTS.md` generation; journal export; inbox/triage behavior; doc-extraction check |
| 3. Persona | OpenExecutive persona (as SOUL.md) + triage prompt, toggled from config |
| 4. Hardening | Backups verified (incl. `data/project/` + both DBs), cost review (OpenRouter console), conversational audit dry-run ("compare members vs cron jobs") |
| 5. Escalation | Optional vector RAG plugin (+sqlite-vec) if corpus outgrows agentic search |

**Deployment notes:** upstream compose builds from source (`build: .`), uses
`network_mode: host` and `HERMES_UID/GID` mapping (s6 drops privileges). The template pins
`HERMES_REF`, sets UID/GID so `data/` stays owned by the Pi user, and needs no inbound
ports (the gateway long-polls Telegram). Expect a slow one-time image build on the Pi
(compiled SQLite + Python deps) — fine on 8 GB.

## 6. Risks

1. **Kanban + plugin are young** — phase-1 gate: confirm ARM64 + how toolsets are enabled.
2. **No built-in RAG** — fine at this scale; [#531](https://github.com/NousResearch/hermes-agent/issues/531) / [#844](https://github.com/NousResearch/hermes-agent/issues/844) track native support.
3. **Multi-tenant context** ([#34352](https://github.com/NousResearch/hermes-agent/issues/34352)) — accepted; DMs semi-public; team of 4 trusts each other. Escape hatch if it ever bites (not planned): per-user profile routing (unvalidated) or per-member bot instances.
4. **Timezone** — Hermes anchors *all* cron scheduling to its single configured zone; there is **no per-job timezone** ([#26255](https://github.com/NousResearch/hermes-agent/issues/26255) closed: native global `timezone` config exists, still not per-job). We anchor UTC and compute per-member schedules in the plugin; the DST recompute is conversational (cron sessions cannot manage cron jobs).
5. **Cloud-only inference** — bot silent if API/internet down; accepted.
6. **Update churn** — no published image; compose builds source at a pinned `HERMES_REF`; deliberate upgrades.
7. **License hygiene** — MIT template code + `THIRD_PARTY_NOTICES.md`; extracted prompts stay Apache 2.0 (upstream ships no NOTICE file; attribution notices retained instead).
8. **Cron drift** — no automated reconciler by design: the digest makes missed check-ins visible, and the conversational audit fixes deltas on demand.
9. **Bisync recovery** — advanced command; mitigated by frequent runs, no blind `--resync`, README runbook, and backups.

## 7. Resolved decisions (formerly open items)

1. **Template license: MIT** (matches Hermes; simplest for a template) +
   `THIRD_PARTY_NOTICES.md` for the Apache 2.0 extracts.
2. **Coordinator plugin: Python toolset** — `register_tool(..., toolset="coordinator")`
   is the documented first-class path; stdlib covers SQLite and timezone math.
3. **Self-service boundary:** each member manages **their own** check-in time by DM
   (the agent matches the sender's `telegram_id` to their member row); roster admin
   (`member_add`, deactivation) stays owner-only.

## 8. Runbooks: first boot & new members

**Onboarding philosophy (YAGNI):** the founding team is known before boot — no interactive
bootstrap interview, no boot-mode agent machinery. The roster comes from
`config/members.seed.yaml` (typed once), the mission from `docs/product/brief.md` (filled
once), and the agent meets a fully-formed project on its first conversation. Everyday
membership changes *after* boot are conversational (`member_add`) — that machinery exists
anyway for daily use; boot reuses nothing new.

### 8.1 First boot — the ~45-minute checklist (owner, once)

1. Pi 5 + SSD, 64-bit OS, Docker installed.
2. `@BotFather` → create bot → token → `.env` (`TELEGRAM_BOT_TOKEN`).
3. Each member sends their Telegram user ID (via any "what's my ID" bot) → one line in
   `TELEGRAM_ALLOWED_USERS` (4 IDs, done once).
4. OpenRouter API key → `.env`; set the $20 monthly credit limit on the key in the console.
5. Google Cloud: OAuth client (Desktop app) + Drive API enabled → `rclone config` →
   refresh token → `.env`; optional root folder id for scope.
6. Fill `config/config.yaml` (project name, drive_root, timezone anchor).
7. Fill `config/members.seed.yaml` (name · telegram_id · timezone · wake · role, ×4).
8. Fill `docs/product/brief.md` from `templates/brief.md` — what we're building, success
   criteria, constraints. **This is how the bot understands what the team is thriving
   for**; AGENTS.md's query map points at it first.
9. `./setup.sh && docker compose up -d` — validates config against the schema, writes
   rclone.conf, installs SOUL.md, pins `cron.model` + `timezone: UTC`, seeds the DB.
10. Verify and go live: bot online → DM it *"create the daily digest at 18:00 Berlin to
    this chat"* (digest job, delivery target = this chat). When the team group exists:
    add the bot as **group admin** (docs: admin bots see all messages without touching
    BotFather privacy mode) and tell it in the group *"digest goes here"* — the job's
    delivery target is edited, no restart.

Everything from step 10 onward is conversation. No SSH after boot day except upgrades.

### 8.2 Onboarding a future member (~5 min, documented — not coded)

1. New member DMs their Telegram user ID (via @userinfobot or similar) to the owner.
2. Owner appends the ID to `TELEGRAM_ALLOWED_USERS` in `.env`, then
   `hermes gateway restart` (or `docker compose restart gateway`). ~60 s. The gate stays
   manual on purpose: **the agent never edits its own authorization**.
3. Owner DMs the bot: *"add Rita — backend, America/Guayaquil, wake 09:00"*.
4. `member_add` writes the row and returns the ready `cronjob` call (UTC schedule
   pre-computed via zoneinfo); the agent relays it verbatim → `checkin-5` live from the
   next cron tick.
5. Optional: welcome note in the team group; the next digest includes her row.
   Leaving: *"deactivate Rita"* → `member_update(active=0)` and her job paused by name.

## 9. Changelog v4 → v5

- Timezone: UTC anchor; schedules computed in the plugin (`zoneinfo`), relayed verbatim by
  the agent; DST handled by a twice-a-year conversational recompute. **`reconcile-cron.sh`
  removed** — drift is caught by the digest and an on-demand conversational audit.
- Settings table restored (digest_time, digest_chat, nudge_limit) — agent-editable live,
  plugin defaults, **no precedence layer**. Toolset: 10 → 7 tools.
- `monthly_budget_usd` removed; the $20 cap is the OpenRouter per-key limit (console).
- `cron.model` pin set by `setup.sh` (drift-guard immunity for scheduled jobs).
- Bisync kept (agent authors docs) with cadence + guardrails; editorial policy: inbox +
  agent triage with group summary.
- SQLite: WAL + busy_timeout; `UNIQUE(member_id, date)` latest-wins on checkins.
- Phase 4 (per-member profiles) dropped; personas: 9 → 8; persona installed as `SOUL.md`;
  QUALITY_REVIEWER gate + six skills deferred; THIRD_PARTY_NOTICES.md instead of NOTICE.
- `HERMES_REF` pin mechanism documented (no published image exists).
- Onboarding: conversational bootstrap interview **rejected (YAGNI)** — founding team is
  known at setup; seed file + `docs/product/brief.md` + runbooks (§8) instead. Telegram
  allowlist vars (`TELEGRAM_ALLOWED_USERS`, group-scoped) added to the env inventory.
