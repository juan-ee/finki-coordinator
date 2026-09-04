# Hermes — Async Project Coordinator Template (v6)

**Goal:** a *clonable template repo* that anyone can copy, fill two config files, and run on
their own hardware: an always-on Telegram coordinator with a Google Shared Drive as the
knowledge source — and **no restart needed for everyday changes**.

**Confirmed decisions (rounds 1–5):**
- Pi **5, 8 GB RAM**, 64-bit OS on SSD, 40 GB free · Docker Compose, built from source at a
  **pinned `HERMES_REF`** (no published image exists upstream).
- Model via **OpenRouter** (Hermes 4 70B, `nousresearch/hermes-4-70b`) · **$20/month cap
  enforced as an OpenRouter per-key limit, set once in the console** (README checklist;
  re-apply whenever the key is rotated) · DMs allowed · dedicated Drive account.
- Members → **SQLite DB, queried live by agent tools**. Kanban (built-in) handles tasks.
- **Two-class configuration:** anything the team can *say out loud* (digest chat, nudge
  limit, member wake times, cron schedules) lives in SQLite and is agent-editable live —
  no restart, no SSH. Anything that is a **money or infrastructure valve** (model id,
  group id, drive root, RAG toggle) lives in `config.yaml` and stays operator-owned.
  The agent is the only day-to-day interface; nobody on the team edits files to change behavior.
- **No helper processes.** No reconciler script, no daemons: the plugin computes, the agent
  relays, the daily digest makes drift visible.
- Secrets in `.env` (gitignored). Template follows best practices.

**Round 5 — the v6 restructure (owner-confirmed 2026-09-03, per
[docs/audit/2026-09-03-audit.md](docs/audit/2026-09-03-audit.md), directives D1–D4):**
- **D1 — Onboarding is door-first.** The `TELEGRAM_ALLOWED_USERS` gate stays
  operator-owned: `scripts/allow.sh <id>...` appends missing IDs to `.env` and applies
  with `docker compose up -d` — **never `restart`**, which reuses the env frozen at
  container creation. The bot never edits its own door. `member_add` then requires the
  sender's `telegram_id`; duplicate active-member names are rejected; an owner-only
  `member_delete` hard-removes a row.
- **D2 — Drive without rclone.** The Drive folder is the team's memory; the agent is its
  librarian on Hermes' built-in **google-workspace skill ($GAPI)** plus a local SQLite
  FTS5 index: `knowledge_sync` (incremental DOWN by `modifiedTime` watermark into a
  rebuildable cache), `knowledge_search` (READ: top chunks, then confirm against the
  live file), `$GAPI drive upload` (UP: journals/digests/agent docs — core, not
  deferred). Bisync, `sync.sh`, the host crontab, and the recovery runbook are removed.
  Toolset 7 → 10.
- **D3 — Seed hygiene.** The committed seed carries placeholders; real Telegram IDs are
  never committed.
- **D4 — Dead weight cut.** `status_days` column, `digest_time` setting, compose Drive
  env passthrough, `scripts/sync.sh` + its tests, setup.sh's rclone step, the
  bisync-recovery runbook task.

---

## 1. Runtime model: config for boot, DB for everything live

**Principle:** *boot defaults* in `config.yaml` (validated, changes need a restart) vs
*live state* in SQLite (changes take effect on the next tool call — no restart).

```
             ┌─────────────────────────────────────────────────────┐
             │  data/hermes/hermes-coord.db  (SQLite, live state)  │
             │   members · checkins · settings · knowledge cache   │
             │   (FTS5) — read/written by the plugin tools         │
             └──────────────┬──────────────────────────────────────┘
                            │ tools: member_add/update/list/delete,
                            │ checkin_submit, checkins_by_date,
                            │ setting_get/set, knowledge_sync/search
               Telegram     │   (agent calls them; humans talk to the bot)
     "I'm Leo (id 123…), Berlin, wake 10:00"  ─────►  INSERT member
                            │        + tool returns a ready-made cronjob call
                            │        + agent relays it → job `checkin-4` live
```

- **Onboarding is door-first (v6).** The access gate is `TELEGRAM_ALLOWED_USERS` — a
  compose env var frozen at container creation. The door is opened by the **operator**:
  `scripts/allow.sh <id>...` appends missing IDs to `.env` (idempotent, other lines
  untouched, no other values printed) and applies with `docker compose up -d` — never
  `docker compose restart`, which reuses the frozen env and would silently ignore the
  new line (this corrects the v5 §8.2 doc bug). The bot never edits its own
  authorization. Once the door is open, the member DMs the bot; the agent takes the
  sender's Telegram ID from session context and creates the **COMPLETE** row via
  `member_add` — `telegram_id` is required (schema, tool description, handler).
  Duplicate active-member names are rejected with an actionable summary (complete the
  existing row via `member_update` instead). `member_delete` is owner-only (enforced
  at the persona layer — the plugin has no caller identity; same basis as the
  roster-admin rule) and hard-removes the row, its check-ins, and its check-in job.
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
- **Settings** (`digest_chat`, `nudge_limit`) live **only** in the `settings`
  key-value table; the plugin ships hardcoded defaults for missing keys — **no precedence
  rules**. The digest schedule is edited conversationally (`cronjob` edit by job name) —
  the v5 `digest_time` setting was a dial connected to nothing and is dropped in v6; the
  recorded alternative (a `cron_relay` from `setting_set` if a dial is ever wanted)
  stays a Notes entry, not code.
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
**10 tools**:
`member_add` · `member_update` · `member_list` · `member_delete` · `checkin_submit` ·
`checkins_by_date` · `setting_get` · `setting_set` · `knowledge_sync` · `knowledge_search`.
(Cuts vs v4: `member_deactivate` → `member_update` with `active=0`; `member_get` →
`member_list` filtered; `board_snapshot` → the `kanban_*` toolset already covers it.
v6 additions (D1/D2): `member_delete`, `knowledge_sync`, `knowledge_search`.
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
  key   TEXT PRIMARY KEY,                  -- digest_chat, nudge_limit
  value TEXT
);
-- Knowledge cache (v6): rebuildable from Drive at any time — the index, not the record.
CREATE TABLE knowledge (
  chunk_id       INTEGER PRIMARY KEY,
  file_id        TEXT NOT NULL,   -- Drive file id (stable across renames)
  path           TEXT NOT NULL,   -- logical path within the Drive root
  title          TEXT NOT NULL,
  heading        TEXT,            -- section heading (chunk label; NULL = preamble)
  body           TEXT NOT NULL,
  modified_time  TEXT NOT NULL,   -- Drive modifiedTime — the sync watermark source
  fetched_at     TEXT NOT NULL,
  UNIQUE(file_id, heading)         -- per-file reindex = DELETE + reINSERT, idempotent
);
CREATE INDEX knowledge_file ON knowledge(file_id);
-- External-content FTS5 over the cache rows: one text store, no duplication, no
-- triggers (the sync owns writes). unicode61 + remove_diacritics 2 makes matching
-- accent-insensitive: MATCH 'decision' finds "decisión".
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
  title, body,
  content='knowledge',
  content_rowid='chunk_id',
  tokenize='unicode61 remove_diacritics 2'
);
-- Ranking: title matches weigh 10:1 above body matches.
-- SELECT chunk_id, bm25(knowledge_fts, 10.0, 1.0) AS rank
--   FROM knowledge_fts WHERE knowledge_fts MATCH ?
--   ORDER BY rank LIMIT 3;
```

Bootstrap: `scripts/init_db.py` seeds from `config/members.seed.yaml` **once** on first
boot, then the DB is authoritative; the committed seed is a **placeholder example** only
— real Telegram IDs are never committed (v6, D3): they enter via `allow.sh` plus the
door-first flow, or a gitignored local seed file if the founding roster is pre-known.

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
                                   # (static gate, applied with `docker compose up -d` —
                                   # add members later with: scripts/allow.sh <id>...)
# TELEGRAM_GROUP_ALLOWED_CHATS=    # optional: team group chat id the bot may interact with
# TELEGRAM_GROUP_ALLOWED_USERS=    # optional: group-only senders (no DM access)

# ── Model API (OpenRouter) ─────────────────────────────────
OPENROUTER_API_KEY=                # https://openrouter.ai/keys
                                   # → set a $20 monthly credit limit on this key
                                   #   in the OpenRouter console (README step 4)

# ── Google Drive ───────────────────────────────────────────
# No Drive env vars (v6): the agent connects through Hermes' built-in
# google-workspace skill ($GAPI) — agent-driven setup, skill-managed credential,
# auto token refresh. In-container verification is the phase-2 gate's FIRST item.
```

**Full env inventory (secrets vs config):**

| Env var | Secret? | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram gateway |
| `TELEGRAM_ALLOWED_USERS` | — | who may DM/trigger the bot ([Hermes allowlist](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram)); extended by `scripts/allow.sh` (door-first onboarding, §8.2) |
| `TELEGRAM_GROUP_ALLOWED_CHATS` | — | authorized team group chat id (digest delivery target) |
| `TELEGRAM_GROUP_ALLOWED_USERS` (alt) | — | group-only senders, without DM access |
| `OPENROUTER_API_KEY` | ✅ | LLM calls (monthly limit set in OpenRouter console) |
| `HERMES_UID` / `HERMES_GID` | — | s6 privilege-drop ids so bind-mount files stay owned by the Pi user |

No `GOOGLE_DRIVE_*` or `RCLONE_*` variables exist in v6: Drive access is skill-managed
(`$GAPI`) inside the container — no OAuth clients, refresh tokens, service accounts, or
rclone remotes to configure, and nothing Drive-related passes through compose.

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
`nudge_limit`, `digest_chat`, per-member timezone/wake, all cron schedules, and the
knowledge-sync watermark.

---

## 3. Knowledge: Drive is the record; the agent is its librarian (v6)

**Layout (owner decision 2026-09-03):** Google Drive holds the team-edited docs — the
record. The Pi holds no mirror: the local side carries only the agent-owned, rebuildable
cache + index (the `knowledge`/`knowledge_fts` tables inside `hermes-coord.db`, §1)
and the gateway working directory `data/project/` — the agent workspace whose authored
files are uploaded to Drive after writing. Deleting the local cache = a full resync; it
is an index, not the record.

```
Google Drive (the record — team-edited):     data/project/ (agent workspace, local):
├── docs/                                    ├── AGENTS.md   ← GENERATED (roster +
│   ├── product/brief.md                     │                  structure + query map)
│   │     (mission — read before major asks) ├── README.md   ← human onboarding; static
│   ├── decisions/  ← ADRs (numbered)        ├── journal/    ← daily digests (written
│   ├── meetings/   ← dated notes            │                  locally, uploaded UP)
│   └── howto/      ← operational playbooks  ├── inbox/      ← drop zone; triage files
├── assets/         ← images, binaries       │                  into Drive docs/**
└── people/         ← optional per-member    ├── templates/  ← brief/adr/meeting-notes
                     notes (never the roster) └── .archive/   ← moved, never deleted
```

**The loop — DOWN · INDEX · READ · UP (D2):**
- **DOWN — `knowledge_sync`** (coordinator tool): incremental by Drive `modifiedTime`.
  Call 1 (plan, empty payload) returns the stored watermark; the agent lists the Drive
  root via `$GAPI`, filters files whose `modifiedTime` passed the watermark, and
  downloads those; call 2 (ingest) passes the fetched files in; the tool chunks each
  file, rewrites its cache rows (per-file reindex = DELETE + reINSERT), and advances the
  watermark — derived state (`MAX(modified_time)` over cached rows), so there is no
  knob for the agent to clobber.
- **INDEX — SQLite FTS5, external-content table** (DDL in §1): `unicode61
  remove_diacritics 2` (accent-insensitive: `MATCH 'decision'` finds "decisión"),
  `bm25()` ranking with title weighted 10:1 above body, one chunk per markdown `##`
  section (heading-less documents = one chunk; duplicate headings get an occurrence
  suffix so `UNIQUE(file_id, heading)` holds), no overlap, no embeddings, no prefix
  indexes — YAGNI until a real query misses. `INSERT INTO knowledge_fts(knowledge_fts)
  VALUES('integrity-check')` is the phase-gate verification command.
- **READ — `knowledge_search`** (coordinator tool): returns the top chunks
  (file_id / path / title / heading). The agent then reads the **live original on
  Drive** (`$GAPI` download/get) before quoting it — the index is a finding aid;
  Drive is current truth.
- **UP — `$GAPI drive upload`:** journals, digests, and structured docs the agent
  writes ARE uploaded to Drive (core feature, not deferred). Google Drive's built-in
  version history is the conflict safety net — no bisync, no rclone, no host crontab,
  no recovery runbooks.

**Editorial policy (unchanged in spirit):** the agent files drafts into `inbox/`; the
weekly triage moves them into the Drive `docs/**` (via `$GAPI upload`) and posts a
*"what I filed"* summary to the group — humans keep editorial control with near-zero
effort. `journal/`, `inbox/` and `.archive/` remain agent-writable; Drive `docs/`
placement always goes through triage.

**Query map (written into AGENTS.md):** mission & goals → `docs/product/brief.md` on
Drive (read before any major ask; found via `knowledge_search` or a live `$GAPI`
read); questions about the project → `knowledge_search`, then confirm against the live
file; status/activity → `journal/` by date or the `checkins_by_date` tool; tasks →
`kanban_*` tools (never files); "what's new" → `inbox/` triage; who/availability →
`member_list` (never a file).

**Escalation triggers (documented, NOT implemented):** embeddings/vector RAG (the corpus
outgrows FTS5 — phase-5 spike only), prefix indexes (a real query misses on prefix
search), standalone sync scripts (volume outgrows agent-mediated sync). Each stays a
written trigger until the team decides.

**Loading:** the gateway runs with `data/project` as working directory, so `AGENTS.md`
is injected into DM sessions automatically (Hermes discovers context files from the
session CWD). Cron jobs that need it pass `workdir=` (those then run sequentially —
fine at this scale).

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
MEMORY.md/USER.md · optional LLM-Wiki skill · **google-workspace skill ($GAPI)** —
`drive search/get/download/upload`, agent-driven setup, auto token refresh, native-file
export to text-friendly formats (v6: the entire knowledge loop rides on it; verified
upstream against the docs + repo at our pin's lineage — in-container verification at our
HERMES_REF is the phase-2 gate's FIRST item).
**No built-in vector RAG** — the local FTS5 index (v6) covers text search at this scale;
the sqlite-vec plugin stays a phase-5 escalation.

---

## 5. Phases

| Phase | Deliverable |
|---|---|
| 0. Foundation | Repo skeleton: compose building hermes-agent at pinned `HERMES_REF`; `.env.example`; config schema + validation; `setup.sh` (rclone conf, SOUL.md install, Hermes config: model + `cron.model` + `timezone: UTC`); `init_db`; README checklist incl. OpenRouter key limit; Pi prepared |
| 1. Core bot | Hermes + Telegram + kanban wired; coordinator plugin (7 tools); daily check-in crons + digest job. **Gates: ARM64 build · kanban + plugin live · timezone matrix (Guayaquil + Berlin, incl. a DST edge) · `cron.model` pin verified · AGENTS.md/SOUL.md wiring** |
| 2. Knowledge (v6) | Door-first member lifecycle: `allow.sh`, `member_add` rework, `member_delete`, seed hygiene; dead-weight cut (status_days, digest_time, sync.sh, Drive env); knowledge tables + `knowledge_sync`/`knowledge_search`; $GAPI gate. **Gates: $GAPI in-container FIRST · FTS5 diacritics + integrity-check · upload/down round-trips · digest end-to-end** |
| 3. Persona | OpenExecutive persona (as SOUL.md) + triage prompt, toggled from config |
| 4. Hardening | Backups verified (`data/project/` agent workspace + both DBs — the knowledge cache is rebuildable, so its backup is convenience, not necessity), cost review (OpenRouter console), conversational audit dry-run ("compare members vs cron jobs") |
| 5. Escalation | Optional vector RAG plugin (+sqlite-vec) if corpus outgrows agentic search |

**Deployment notes:** upstream compose builds from source (`build: .`), uses
`network_mode: host` and `HERMES_UID/GID` mapping (s6 drops privileges). The template pins
`HERMES_REF`, sets UID/GID so `data/` stays owned by the Pi user, and needs no inbound
ports (the gateway long-polls Telegram). Expect a slow one-time image build on the Pi
(compiled SQLite + Python deps) — fine on 8 GB.

## 6. Risks

1. **Kanban + plugin are young** — phase-1 gate: confirm ARM64 + how toolsets are enabled.
2. **No built-in vector RAG** — the local FTS5 index (v6) covers text search at this
   scale; [#531](https://github.com/NousResearch/hermes-agent/issues/531) / [#844](https://github.com/NousResearch/hermes-agent/issues/844) track native support; sqlite-vec stays the documented phase-5 escalation.
3. **Multi-tenant context** ([#34352](https://github.com/NousResearch/hermes-agent/issues/34352)) — accepted; DMs semi-public; team of 4 trusts each other. Escape hatch if it ever bites (not planned): per-user profile routing (unvalidated) or per-member bot instances.
4. **Timezone** — Hermes anchors *all* cron scheduling to its single configured zone; there is **no per-job timezone** ([#26255](https://github.com/NousResearch/hermes-agent/issues/26255) closed: native global `timezone` config exists, still not per-job). We anchor UTC and compute per-member schedules in the plugin; the DST recompute is conversational (cron sessions cannot manage cron jobs).
5. **Cloud-only inference** — bot silent if API/internet down; accepted.
6. **Update churn** — no published image; compose builds source at a pinned `HERMES_REF`; deliberate upgrades.
7. **License hygiene** — MIT template code + `THIRD_PARTY_NOTICES.md`; extracted prompts stay Apache 2.0 (upstream ships no NOTICE file; attribution notices retained instead).
8. **Cron drift** — no automated reconciler by design: the digest makes missed check-ins visible, and the conversational audit fixes deltas on demand.
9. **$GAPI is a hard dependency (v6)** — the whole knowledge loop rides Hermes'
   google-workspace skill at our pinned ref. The phase-2 gate verifies it in-container
   FIRST; if it does not work, the phase stops and reports — no rclone workaround exists
   by design. Accepted residuals: a Drive-side deletion leaves stale cache chunks until
   the next full resync (the cache is rebuildable; searches still confirm against the
   live file), and $GAPI rate limits bound sync/upload volume (fine for a team of 4 and
   dozens of text docs).

## 7. Resolved decisions (formerly open items)

1. **Template license: MIT** (matches Hermes; simplest for a template) +
   `THIRD_PARTY_NOTICES.md` for the Apache 2.0 extracts.
2. **Coordinator plugin: Python toolset** — `register_tool(..., toolset="coordinator")`
   is the documented first-class path; stdlib covers SQLite and timezone math.
3. **Self-service boundary:** each member manages **their own** check-in time by DM
   (the agent matches the sender's `telegram_id` to their member row); roster admin
   (`member_add`, deactivation, `member_delete`) stays owner-only.
4. **Knowledge home (v6):** the `knowledge`/`knowledge_fts` tables live in the SAME
   `hermes-coord.db` — one DB, one backup, one WAL discipline; the cache is rebuildable
   from Drive at any time.
5. **Journals/digests/agent-authored docs are uploaded to Drive (v6)** — core feature;
   Google Drive version history is the conflict safety net.
6. **`hermes config set` for the allowlist: rejected (v6 check).** It CAN write
   env-style keys (routed to `HERMES_HOME/.env`), but our compose injects
   `TELEGRAM_ALLOWED_USERS` from the repo-root `.env` as a container env var, and
   container env always shadows dotenv values — the config-set path would be silently
   ignored. `allow.sh` therefore edits the repo-root `.env` and applies with
   `docker compose up -d`; restart semantics unchanged either way.

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
3. Each member sends their Telegram user ID (via any "what's my ID" bot) →
   `scripts/allow.sh <id>...` appends it and applies (4 IDs, done once — the same
   command onboards every future member, §8.2).
4. OpenRouter API key → `.env`; set the $20 monthly credit limit on the key in the console.
5. Drive: the team's Shared Drive/folder exists and the bot's Drive account can reach
   it — no local OAuth files to create; the agent connects in-container via the
   google-workspace skill ($GAPI) after boot (phase-2 gate verifies).
6. Fill `config/config.yaml` (project name, drive_root, timezone anchor).
7. Fill `config/members.seed.yaml` (name · timezone · wake · role, ×4) — `telegram_id`
   stays `null` in the committed file; real IDs are never committed (D3).
8. Fill `docs/product/brief.md` from `templates/brief.md` — what we're building, success
   criteria, constraints. **This is how the bot understands what the team is thriving
   for**; AGENTS.md's query map points at it first.
9. `./setup.sh && docker compose up -d` — validates config against the schema, installs
   SOUL.md, pins `cron.model` + `timezone: UTC`, enables the plugin/toolsets, seeds
   `data/project/`.
10. Verify and go live: bot online → DM it *"create the daily digest at 18:00 Berlin to
    this chat"* (digest job, delivery target = this chat). When the team group exists:
    add the bot as **group admin** (docs: admin bots see all messages without touching
    BotFather privacy mode) and tell it in the group *"digest goes here"* — the job's
    delivery target is edited, no restart.

Everything from step 10 onward is conversation. No SSH after boot day except upgrades.

### 8.2 Onboarding a future member (~5 min, door-first — documented, not coded)

1. New member DMs their Telegram user ID (via @userinfobot or similar) to the owner.
2. Owner runs the door script: `scripts/allow.sh <id>...` — validates the numeric IDs,
   appends the missing ones to `TELEGRAM_ALLOWED_USERS` in `.env` (idempotent; other
   lines untouched; other values never printed), then applies with
   `docker compose up -d`. **Never `docker compose restart`** — restart reuses the env
   frozen at container creation, so the new allowlist line would be silently ignored
   (this corrects the v5 runbook's restart bug). ~60 s. The gate stays operator-owned on
   purpose: **the agent never edits its own authorization**.
   *(v6 check: `hermes config set` CAN write env-style keys — routed to
   `HERMES_HOME/.env` — but our compose injects `TELEGRAM_ALLOWED_USERS` from the
   repo-root `.env` as a container env var, and container env always shadows dotenv, so
   the config-set path would be silently ignored; the script edits the repo-root `.env`
   directly. Restart semantics unchanged either way.)*
3. Member DMs the bot: *"I'm Rita — backend, America/Guayaquil, wake 09:00."*
4. The agent takes the **sender's Telegram ID from session context** and calls
   `member_add` with the COMPLETE row — `telegram_id` required; a duplicate
   active-member name is rejected with an actionable summary (completing a seeded row
   via `member_update` is the correct path, never a second row).
5. `member_add` writes the row and returns the ready `cronjob` call (UTC schedule
   pre-computed via zoneinfo); the agent relays it verbatim → `checkin-5` live from the
   next cron tick.
6. Optional: welcome note in the team group; the next digest includes her row.
   Leaving: *"deactivate Rita"* → `member_update(active=0)` and her job paused by name;
   or hard-remove via `member_delete` (owner-only) — row, check-ins, and job gone.

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

## 10. Changelog v5 → v6 (owner-confirmed 2026-09-03; per docs/audit/2026-09-03-audit.md)

- **Onboarding is door-first (D1):** new `scripts/allow.sh <id>...` — validate numeric
  IDs → append missing ones to `TELEGRAM_ALLOWED_USERS` in `.env` (idempotent, other
  lines untouched) → apply with `docker compose up -d`, **never `restart`** (restart
  reuses the frozen env — this corrects a v5 §8.2 doc bug). `member_add` now REQUIRES
  `telegram_id` (schema, tool description, handler; the agent takes the sender's ID from
  session context); duplicate active-member names are rejected with an actionable
  summary; owner-only `member_delete` added (persona-enforced; removes row, check-ins,
  and relays job removal).
- **Bisync removed (D2):** no rclone, no `scripts/sync.sh`, no host crontab, no
  recovery runbook, no compose Drive env passthrough, no Drive OAuth/rclone env vars.
  The Drive folder is the team's memory; the agent is its librarian on Hermes' built-in
  google-workspace skill ($GAPI) plus a local SQLite FTS5 index: `knowledge_sync`
  (incremental DOWN by `modifiedTime` watermark into a rebuildable cache in the same
  `hermes-coord.db`), `knowledge_search` (READ — top chunks; the agent then confirms
  against the live file), `$GAPI drive upload` (UP — journals/digests/agent docs are
  core; Drive version history is the conflict safety net). Index: external-content FTS5,
  `unicode61 remove_diacritics 2` (`MATCH 'decision'` finds "decisión"), `bm25()`
  title-weighted 10:1, one chunk per `##` section, per-file reindex = DELETE +
  reINSERT, integrity-check in the gate. Toolset 7 → 10. Escalations (embeddings/vector
  RAG, prefix indexes, standalone sync scripts) stay documented triggers, not
  implementations.
- **Seed hygiene (D3):** the committed seed carries placeholders; real Telegram IDs are
  never committed. The founder ID already in git history is flagged to the owner
  (history purge is the owner's call, out of scope).
- **Dead weight cut (D4):** `status_days` column (versioned migration), `digest_time`
  setting key (the digest schedule is edited conversationally; the relay alternative is
  recorded in Notes, not code), compose Drive env passthrough, `scripts/sync.sh` + its
  tests, setup.sh's rclone.conf step, the T4.2 bisync-recovery runbook task.
- Phase 2 re-queued as the v6 member-lifecycle + knowledge queue (ROADMAP T2.5–T2.17);
  the old bisync gate (former T2.4) is retired — it never completed (abandoned at the
  Drive-OAuth incident) and bisync never reached production, which is what makes the
  deletion safe.
