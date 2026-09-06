# ROADMAP.md — Atomic Implementation Queue

**How to use:** work tasks top-to-bottom, one at a time, following the execution protocol
and Definition of Done in `AGENTS.md`. Never start a task whose dependencies are unchecked.
After every non-DOC task, run the **Review protocol** (AGENTS.md) before marking the box.
`DOC` = markdown/config-only task (no failing test required). `MANUAL-GATE` = requires the
Pi/human; the agent only produces/refreshes the verification script.

| Phase | Exit criteria | Gate |
|---|---|---|
| 0 Foundation | `make check` green on full package; compose validates; CI wired | T0.14 |
| 1 Core bot | Plugin + skills + integration day-flow tests pass | T1.6 manual on Pi |
| 2 Member lifecycle & knowledge (v6) | Door script + 10-tool surface + $GAPI knowledge loop + seed hygiene | T2.17 manual on Pi |
| 3 Persona | SOUL.md toggle + triage rules + license notices | — |
| 4 Hardening | Backup/restore + runbooks | T4.3 manual on Pi |
| 5 Escalation | Spike doc only (do not implement) | — |

---

## Phase 0 — Foundation

- [x] **T0.1 Repo scaffolding (uv)** — `pyproject.toml` (package `coordinator` under
  `src/`, deps: `jsonschema`, `PyYAML`; dev: `pytest`, `pytest-cov`, `ruff`, `mypy`),
  `uv.lock` committed + `.python-version` (3.11), `Makefile` (`install/test/lint/type/check`,
  every target wrapping `uv run` per AGENTS.md Commands), `.gitignore` (`data/`, `.env`,
  `backups/`, `__pycache__/`, `.venv/`), `LICENSE` (MIT), stub `README.md`.
  Tests: `tests/test_smoke.py::test_package_imports` (import coordinator, assert `__version__`).
  Acceptance: `uv sync && make check` exits 0.

- [x] **T0.2 CI workflow** — `.github/workflows/ci.yml`: on push/PR: `astral-sh/setup-uv`
  → `uv sync --frozen` → `uv run make lint && uv run make type && uv run make test` +
  a step validating `config/config.example.yaml` against `config/config.schema.json` via
  `uv run python -m coordinator.config validate`.
  Acceptance: workflow commands are exactly the Makefile targets; YAML parses
  (`tests/test_ci.py::test_workflow_yaml_parses`).

- [x] **T0.3 Config JSON Schema + example** — `config/config.schema.json`
  (draft 2020-12; keys/semantics per `proposal.md` §2: `project{name,drive_root,timezone}`,
  `telegram{group_id}`, `model{provider,default_model}` (no budget key), `rag{enabled,
  chunk_size,embed_model}`, `log_level`; `additionalProperties: false`; `timezone` pattern
  is an IANA-looking `Area/City` string) + `config/config.example.yaml`.
  Tests: `tests/unit/test_config_schema.py` — example validates; invalid variants rejected:
  missing `model.default_model`, `rag.chunk_size` as string, unknown top-level key,
  `timezone: "Not/AZone"`, negative chunk_size.

- [x] **T0.4 `src/coordinator/config.py`** — `load_config(path) -> Config` (frozen
  dataclasses mirroring the schema), validation via jsonschema + `zoneinfo.ZoneInfo`
  probe of `project.timezone`; typed exception `ConfigError` with schema-path in message.
  Tests: `tests/unit/test_config_loader.py` — happy path values land in dataclasses;
  missing file → ConfigError; each invalid variant from T0.3 raises ConfigError naming
  the JSON path; valid tz key passes probe.

- [x] **T0.5 `src/coordinator/schema.sql` + `db.py`** — DDL exactly per `proposal.md` §1
  (`members`, `checkins` with `UNIQUE(member_id, date)`, `settings`). `db.py`:
  `connect(path)` sets `journal_mode=WAL` + `busy_timeout=5000` + `foreign_keys=ON`;
  `migrate(conn)` applies versioned migrations from a `schema_migrations` table.
  Tests: `tests/unit/test_db.py` — pragmas after connect; migrate idempotent (double call
  ok); unknown journal mode never silently set.

- [x] **T0.6 `src/coordinator/repositories.py`** — `MembersRepo`, `CheckinsRepo`,
  `SettingsRepo` (concrete SQLite impls) + `Protocol` interfaces for handlers. Semantics:
  members add/update(active flag)/list(active filter, ordered by name); checkins
  **upsert latest-wins** on (member_id, date); settings `get` falls back to module-level
  `DEFAULTS = {digest_time:"18:00", digest_chat:"dm", nudge_limit:"2"}` when key absent.
  Tests: `tests/unit/test_repositories.py` (tmp_path DB) — CRUD round-trips; second
  `submit` same member+date replaces row (assert new values + one row); `active=0` filtered
  from list but still gettable; settings default returned then overridden.

- [x] **T0.7 `src/coordinator/scheduling.py`** *(crown jewel — write tests first)* —
  pure functions, no I/O:
  `wake_to_cron_expr(wake: "HH:MM", tz: IANA, at_utc: datetime) -> "M H * * *"` (UTC),
  `validate_wake`, `SchedulingError`. DST resolved from `at_utc` only.
  Tests: `tests/unit/test_scheduling.py` —
  `08:00 America/Guayaquil @2026-01-15` → `0 13 * * *`;
  `08:00 Europe/Berlin @2026-01-15` → `0 7 * * *`;
  `08:00 Europe/Berlin @2026-07-15` → `0 6 * * *`;
  boundary instants 2026-03-29 and 2026-10-25 (01:00 UTC) flip the hour;
  `25:00`, `8:00` (no zero-pad), `Mars/Olympus` raise SchedulingError.
  Acceptance: zero `datetime.now()` in module.

- [x] **T0.8 `src/coordinator/handlers.py`** — the 7 tool handlers
  (`member_add, member_update, member_list, checkin_submit, checkins_by_date,
  setting_get, setting_set`) as plain functions taking repo Protocols + `Clock`.
  Payload validation manual + typed; result dict per AGENTS.md contract; schedule changes
  produce `cron_relay` with job name `checkin-<id>` (create on add; edit on wake change;
  **pause** on deactivate) — schedule computed **only** via `scheduling.py`.
  Tests: `tests/unit/test_handlers.py` (in-memory repos + `FakeClock`) — happy path per
  handler; `cron_relay.args` asserted **exactly** (incl. schedule string) for add / wake
  change / deactivate; unknown member, bad timezone, bad wake, wrong-day checkin date
  format → `ok: False` + actionable summary; member self-service rule: handler cannot
  deactivate via `member_add` (no such arg).

- [x] **T0.9 `src/coordinator/hermes_plugin.py`** — thin adapter: `TOOL_SPECS`
  (name → JSON schema dict + handler ref + `toolset="coordinator"`), `register(ctx)`
  calling `ctx.register_tool(...)`. Import of Hermes guarded (adapter only).
  Tests: `tests/unit/test_hermes_plugin.py` — `FakeCtx` records exactly 7 registrations;
  schemas well-formed (`type/properties/required`); dispatch wires to the right handler;
  result dicts pass through untouched.

- [x] **T0.10 `scripts/init_db.py`** — CLI `--db PATH [--seed config/members.seed.yaml]`:
  mkdir -p, connect+migrate, seed members once (marker `seeded_at` in settings), exit 0
  idempotently. Tests: `tests/unit/test_init_db.py` — fresh seed inserts N members;
  second run = no duplicates; run without seed = empty members table.

- [x] **T0.11 `DOC` — config artifacts finalized** — `config/members.seed.yaml`
  (4 members: 3× `America/Guayaquil`, 1× `Europe/Berlin`; founding-team roster
  Juan/Jose/Luis/David per owner direction 2026-09-01 — wakes 06:30/06:00/05:30/11:00,
  `telegram_id` null until each member onboards, Juan's id committed by owner direction —
  see Notes log), `config/config.example.yaml`, `.env.example` (exact content per
  `proposal.md` §2 incl. `TELEGRAM_ALLOWED_USERS` + group-scoped vars + OpenRouter-limit
  comment).
  Tests: `tests/unit/test_seed_file.py` — seed parses; every tz resolvable via zoneinfo;
  wakes match `^([01]\d|2[0-3]):[0-5]\d$`.

- [x] **T0.12 `scripts/generate_agents_md.py`** — renders the **runtime**
  `data/project/AGENTS.md`: roster table (from members DB), folder structure block,
  query map (mission → `docs/product/brief.md` first; decisions → docs/decisions/ …),
  editorial policy summary. Deterministic output; overwrite in place.
  Tests: `tests/unit/test_generate_agents_md.py` — golden-file render from fixture DB
  (contains all member names/timezones + all query-map bullets); rerun is byte-identical.

- [x] **T0.13 `scripts/setup.sh`** — `set -euo pipefail`; steps: check required `.env`
  keys; validate `config/config.yaml` via `python -m coordinator.config validate`;
  write rclone.conf from env (client id/secret/refresh, remote, root folder id);
  install `prompts/persona.md` → `${HERMES_HOME:-$HOME/.hermes}/SOUL.md`; apply Hermes
  config: model + `cron.model` pin + `timezone: UTC` (via `hermes config set`, with a
  printed manual fallback if CLI absent); print next steps. `--dry-run` prints actions
  without writing. Tests: `tests/test_setup_smoke.py` — `bash -n` passes; `--dry-run`
  with fixture env exits 0 and writes nothing (snapshot tmp dir).

- [x] **T0.14 `docker-compose.yml` + `docker/`** — template compose: build hermes-agent
  from upstream repo at ref pinned in `docker/HERMES_REF` (build arg), mount
  `./src/coordinator` into the container's plugin directory, mount `./data` for our DB +
  project mirror, `TZ=UTC`, `HERMES_UID/GID` passthrough, gateway command per upstream
  compose. Document the Pi build (slow, one-time) in `docker/README.md`.
  Tests: `tests/test_compose.py` — `docker compose config` exits 0 (skip if docker
  absent); HERMES_REF file contents appear in the rendered config.
  **Phase-0 gate:** all boxes above checked.

## Phase 1 — Core bot

- [x] **T1.1 `DOC` — `prompts/persona.md`** (→ SOUL.md) — group-PM voice rewritten from
  OpenExecutive `executive_persona.py` (operator tone, 2–3 key variables, end with
  "so, what do we do next"). Must encode: relay `cron_relay` **verbatim** (never recompute
  schedules), read `docs/product/brief.md` before major asks, editorial policy
  (drafts → `inbox/`, never write `docs/` directly), members manage only their own row,
  roster admin owner-only. Acceptance: self-review checklist at file bottom all ✅.

- [x] **T1.2 `DOC` — `prompts/skills/check-in/SKILL.md`** — check-in flow: greet →
  ask done/next/blockers → `checkin_submit` → confirm + surface relevant teammates'
  blockers. One worked example dialogue embedded.

- [x] **T1.3 `DOC` — `prompts/skills/digest/SKILL.md`** — 17:00 job: `checkins_by_date`
  → `kanban_list` → inbox count → write `journal/YYYY-MM-DD.md` (format template embedded:
  Check-ins table with ✅/⚠️, Board moves, Inbox, Flags) → post condensed summary to the
  delivery chat → run `scripts/sync.sh` (on-demand beat, proposal §3).

- [x] **T1.4 `DOC` — `prompts/skills/schedules.md`** — "recalculate all check-in
  schedules" procedure (member_list → build edit relays for every active member → relay);
  twice-a-year DST note; why it must be asked interactively (cron sessions cannot manage
  cron jobs).

- [x] **T1.5 Integration test — `tests/integration/test_day_flow.py`** — a full day on a
  tmp DB: seed 4 members → 4 check-ins (one corrected, assert upsert) → `checkins_by_date`
  returns 4 → set digest settings → assert `cron_relay` schedule strings for Guayaquil and
  Berlin members at a winter instant and a summer instant.

- [x] **T1.6 `DOC` `MANUAL-GATE` — `docs/verify/phase1.md`** — Pi verification script:
  ARM64 build succeeds (record duration); plugin tools visible in a DM session;
  kanban board reachable; timezone matrix (two fake members, `cronjob trigger`, verify
  delivery in expected window); `cron.model` pin visible on jobs; AGENTS.md injected
  (ask the bot for its query map). Leave unchecked for the human.
  *(2026-09-02: gate executed to Sign-off — steps 2-9 ticked with evidence, deviations
  recorded in the gate doc and Notes log; operator signature in phase1.md.)*

### Phase 1 follow-ups — upstream v0.21.0 alignment (queued 2026-09-02, owner direction)

- [x] **T1.7 `scripts/setup.sh` applies the plugin + toolsets runtime config** — new
  step 6: `hermes config set plugins.enabled '["coordinator"]'` and
  `hermes config set toolsets '["hermes-cli", "kanban"]'` — via the `hermes` CLI when
  present, otherwise the printed in-container fallback (same pattern as step 5; renumber
  the steps 1/5..5/5 → 1/6..6/6 and update the docstring). Closes the phase-1 gate
  deviation: upstream gates user plugins behind `plugins.enabled` (opt-in), and the
  kanban tools' check_fn reads the top-level `toolsets` list (the `all` wildcard does
  NOT enable kanban) — a stranger clone would otherwise silently load no coordinator
  tools and no kanban. Update the plugin.yaml header comment to point at setup.sh.
  Tests: `tests/test_setup_smoke.py` — dry-run prints both `config set` lines and still
  exits 0 writing nothing; no secret leakage.
  Notes: `hermes config set plugins.enabled '["coordinator"]'` is a pure config write
  (no on-disk discovery dependency), chosen over `hermes plugins enable coordinator`
  because the plugin dir only materializes inside the container's mount namespace; the
  value re-asserts the template baseline on idempotent re-runs.

- [x] **T1.8 Tool descriptions ride in the JSON schema** — upstream v0.21.0 plugin docs:
  the model-facing description belongs in `schema["description"]`; the
  `register_tool(description=...)` value is ToolEntry registry metadata only and is NOT
  copied back into a schema that lacks one. Our `TOOL_SPECS` pass `description=` while
  the schemas carry none — the model likely sees our 7 tools undescribed. Keep
  `ToolSpec.description` as the single source of truth and inject it into the schema at
  registration time in `register_tools` (no duplication; dispatch unchanged).
  Tests: `tests/unit/test_hermes_plugin.py` — every FakeCtx registration carries
  `schema["description"]` equal to `TOOL_SPECS[name]["description"]`; payload fields
  unchanged.

- [x] **T1.9 Pin bump: `HERMES_REF` → v2026.8.31 (Hermes v0.21.0)** — resolve the release
  tag to its commit SHA (refs/tags → tag object → commit); move BOTH `docker/HERMES_REF`
  and the `docker-compose.yml` build URL; refresh `docker/README.md`'s current-pin note
  and `tests/unit/test_plugin_manifest.py`'s census comment (upstream census is now
  v1+v2; our manifest fields remain valid). Evaluation basis: Notes log 2026-09-02 —
  no breaking changes on gateway/cron/Telegram/plugin surfaces. Manual follow-up on the
  Pi (human): `docker compose build && docker compose up -d`, then the abbreviated gate
  (plugin tools in a DM, one `hermes cron run`, kanban reachable) — procedure per
  `docker/README.md`.
  Tests: `tests/test_compose.py` drift guard (pin file == build URL) stays green.

## Phase 2 — Member lifecycle & knowledge (v6 restructure)

> **v6 note (2026-09-03):** this phase was re-queued by owner directives D1–D4
> (docs/audit/2026-09-03-audit.md). T2.1–T2.3 shipped as built; T2.1's bisync wrapper is
> removed again by T2.11 (bisync never reached production — the old T2.4 gate was retired
> mid-run at the Drive-OAuth incident, before any baseline existed; see Notes log). The
> former T2.4 bisync gate is superseded by T2.17. Tasks run top-to-bottom; T2.5 is the
> DOC task that produced this queue and the v6 proposal.

- [x] **T2.1 `scripts/sync.sh`** — bisync wrapper: logs to `data/logs/sync.log`, boot mode
  flag, refuses `--resync` without `--i-know-what-im-doing`, uses `RCLONE_REMOTE` +
  `RCLONE_ROOT_FOLDER_ID` from env. Tests: `tests/test_sync_smoke.py` — `bash -n`;
  dry-run without env vars exits non-zero with actionable message.
  *(v6: superseded — the wrapper is deleted with its tests by T2.11 per D4.)*

- [x] **T2.2 `DOC` — `templates/`** — `brief.md` (mission/goals/success criteria/
  constraints), `adr.md` (context/decision/consequences), `meeting-notes.md`,
  `proposal.md`.

- [x] **T2.3 `DOC` — `project-template/` + setup copy step** — seed content for
  `data/project/`: `README.md` (human onboarding), `docs/index.md` stub, empty
  `inbox/ assets/ people/ journal/ .archive/`; `setup.sh` copies it into `data/project/`
  on first boot (never overwrites existing files).

- [x] **T2.5 `DOC` — v6 restructure docs (proposal + this queue)** — amend
  `proposal.md` (title/decisions round 5; §1 door-first onboarding, 10-tool surface,
  v6 schema incl. `knowledge`/`knowledge_fts` DDL; §2 env inventory without the
  Drive/rclone vars + allow.sh pointer; §3 knowledge layout rewrite (Drive = record,
  local = cache/index + agent workspace, DOWN/INDEX/READ/UP loop, escalation triggers);
  §5 phases; §6 risks 2/9; §7 v6 decisions incl. the `hermes config set` allowlist
  check result; §8.1/§8.2 runbooks incl. the `up -d`-not-`restart` correction; §10
  changelog v5→v6) and re-queue this phase (T2.6–T2.17). Two stale `AGENTS.md` lines
  amended to match (mission sentence; scripts/ repo-map entry) — flagged to the owner.
  No code, no tests. Acceptance: proposal, ROADMAP and AGENTS.md agree on v6.

- [x] **T2.6 — Seed hygiene (D3)** — `config/members.seed.yaml`: every `telegram_id`
  becomes `null` (placeholders; real Telegram IDs are NEVER committed — the founder ID
  currently in the file is flagged to the owner; history purge is the owner's call, out
  of scope); header comment rewritten for the door-first flow (IDs enter via
  `scripts/allow.sh` + `member_add`/`member_update`, never the seed; a pre-known
  founding roster may live in a gitignored local seed file). README quickstart step 3
  stops telling strangers to enter real IDs.
  Tests FIRST (red): `tests/unit/test_seed_file.py` — a placeholder-hygiene test
  (every committed `telegram_id` is None) replaces the current null-or-integer
  acceptance. Acceptance: `make check` green.

- [x] **T2.7 — `scripts/allow.sh` door script (D1)** — `scripts/allow.sh <id>...`:
  every arg must be numeric (digits only; otherwise usage + exit 2); loads repo-root
  `.env`; appends each ID missing from `TELEGRAM_ALLOWED_USERS` (comma-join; creates
  the line if absent; idempotent — a second run changes nothing); rewrites `.env`
  preserving every other byte; prints ONLY key names + the allowlist diff — never
  another key's value; applies with `docker compose up -d` (never `restart` — restart
  reuses the env frozen at container creation; the output says so). `--dry-run` prints
  actions, writes nothing. Design decision (proposal §7.6): `hermes config set` CAN
  write env-style keys, but the compose-injected container env shadows it — the script
  edits the repo-root `.env` directly.
  Tests: `tests/test_allow_smoke.py` — `bash -n`; non-numeric rejected; idempotent
  rerun byte-identical; other lines preserved byte-for-byte; no non-allowlist secret
  printed; `--dry-run` writes nothing and names `docker compose up -d`; absent/empty
  `TELEGRAM_ALLOWED_USERS` handled.
  Docs in the same commit: `.env.example` pointer, README quickstart note,
  docker-compose `TELEGRAM_ALLOWED_USERS` comment.

- [x] **T2.8 — `member_add` rework (D1)** — `telegram_id` REQUIRED end to end:
  `handlers.member_add` fails without it (actionable summary: take the sender's ID from
  session context); `TOOL_SPECS["member_add"]` schema `required` gains `telegram_id`
  and the description says where it comes from (the door is operator-run via
  `allow.sh`). Duplicate active-member names rejected with an actionable summary
  (case-insensitive; the summary names the existing row and points at
  `member_update`); a duplicate against an INACTIVE row still adds (D1 letter; edge
  noted in Notes).
  Tests FIRST: `tests/unit/test_handlers.py` (add without telegram_id → ok:False
  naming it; duplicate active name exact + case variant → ok:False with the row id;
  inactive-name duplicate → ok:True); `tests/unit/test_hermes_plugin.py` (required
  list + description). Docs in the same commit: `prompts/persona.md` hard-rule 4
  gains the onboarding rule (door first; then the sender's ID completes the row).

- [x] **T2.9 — `member_delete` tool (D1)** — owner-only hard removal (tool 8/10):
  `handlers.member_delete` (payload `{member_id}`) deletes the member's checkins rows
  then the member row in one transaction (FK enforcement fixes the order); returns
  `cron_relay {"action": "remove", "name": "checkin-<id>"}` when the row had a wake
  (wake ⇒ a job existed; `cronjob` remove accepts a job name); no relay for wake-less
  rows; unknown id → ok:False. Owner-only is enforced at the persona layer (hard rule 4
  — the plugin has no caller identity; same basis as the roster-admin rule); technical
  enforcement stays a documented escalation, not scope.
  `TOOL_SPECS["member_delete"]` + `MembersRepository.delete()` + SQLite impl.
  Tests FIRST: handler (happy path removes member+checkins, relay asserted exactly;
  unknown id; wake-less no relay), repo (`delete` True/False, checkins gone), plugin
  (8 registrations, schema). Docs in the same commit: persona rule 4 mention; README
  tool line (8).

- [x] **T2.10 — Dead weight: `status_days` + `digest_time` (D4)** — versioned
  migration 002 drops `members.status_days`; `db._MIGRATIONS` gains
  callable-migration support (guarded drop: fresh v6 DBs — which never had the column —
  no-op; v5 DBs converge) + a convergence test (fresh-path and upgrade-path
  `sqlite_master` identical). `repositories.py`: `Member` dataclass, column lists,
  add/update signatures lose `status_days`. `DEFAULTS` loses `digest_time`
  (`setting_get`/`setting_set` reject it via the unknown-key path);
  `_setting_value_error` drops the digest_time branch. Tests FIRST: migration tests
  (v5-shaped DB → column gone; fresh DB → 002 no-op), DEFAULTS/rejection tests flip.
  Notes: the recorded alternative for a digest dial (a `cron_relay` from
  `setting_set`) stays documented, not implemented.

- [x] **T2.11 — Remove `scripts/sync.sh` + its tests (D4)** — delete `scripts/sync.sh`
  and `tests/test_sync_smoke.py` in the same commit (`make check` stays green — rule 5).
  `prompts/skills/digest/SKILL.md` step 6 becomes the upload step ("upload the fresh
  journal entry to Drive via `$GAPI drive upload`; if the upload fails, say so in one
  line at the end of the journal entry — drift must be visible, not silent") and the
  sync-failure tone note follows. README architecture lines (bisync mentions) rewritten
  to the v6 loop. Notes: the $GAPI upload path is verified in-container by T2.17 (FIRST
  item) — between T2.11 and the gate the digest upload instruction is doc-forward by
  design.

- [x] **T2.12 — Drive env + rclone cleanup (D4)** — `docker-compose.yml` drops the six
  Drive/rclone env passthroughs (comments updated: Drive access is $GAPI with a
  skill-managed credential; `data/project/` is the agent workspace, not a mirror).
  `tests/test_compose.py` FIRST gains the env set-equality drift guard (rendered
  environment keys == PASSTHROUGH_VARS — pays off the T0.14 note); that failing
  assertion is the red that forces the removal. `scripts/setup.sh` loses step 3
  (rclone.conf write), the Drive trio in REQUIRED_KEYS, and the RCLONE_* knobs
  (renumber 1/6..6/6; docstring). `tests/test_setup_smoke.py` updated (no rclone
  artifacts; REQUIRED_ENV updated). `.env.example` loses the Google Drive section.
  README quickstart env list + Requirements Drive line updated ($GAPI; no local OAuth
  files) — INCLUDING the quickstart step-4 rclone.conf sentence (T2.11 routing) — plus
  the `docker/README.md` mounts-table "rclone-bisynced project mirror" line (T2.11
  routing, parity with the compose comment).

- [x] **T2.13 — Knowledge tables + chunker (D2)** — `schema.sql` gains `knowledge` +
  `knowledge_fts` exactly per proposal §1 (external-content FTS5,
  `unicode61 remove_diacritics 2`, `UNIQUE(file_id, heading)`); versioned migration
  003 (IF NOT EXISTS forms; fresh+upgrade convergence test like T2.10); new pure module
  `src/coordinator/knowledge.py`: `chunk_markdown(title, body)` — one chunk per
  markdown `##` section (`###` content stays inside its `##` chunk), leading
  preamble = one NULL-heading chunk, heading-less documents = one chunk, no overlap;
  duplicate headings disambiguated with an occurrence suffix so
  `UNIQUE(file_id, heading)` holds (documented interpretation); **non-text documents**
  (audit second-pass rule): ingested with NO content → one cache row, empty body —
  title/path only, the index never pretends to hold text it cannot reliably extract.
  `repositories.py`:
  `KnowledgeRepository` Protocol + SQLite impl — `replace_file(...)` (per-file
  reindex: FTS delete → row delete → row insert → FTS insert), `watermark()` =
  `MAX(modified_time)` (derived state; nothing for the agent to clobber),
  `search(query, limit)` = FTS MATCH ordered by `bm25(knowledge_fts, 10.0, 1.0)`.
  Tests FIRST: chunker cases; per-file replace idempotence; the audit's TDD anchor —
  `MATCH 'decision'` finds a chunk containing "decisión"; title-vs-body bm25 ranking;
  `'integrity-check'` passes on a consistent index and RAISES after an out-of-band
  knowledge-row delete.

- [x] **T2.14 — `knowledge_sync` tool (D2)** — two-call protocol, agent-mediated $GAPI
  (pure core; no network, no new deps): call 1 `{}` (plan) →
  `data: {watermark, files_hint}` — the agent lists the Drive root via `$GAPI`,
  filters files with `modifiedTime` past the watermark, downloads those; call 2
  `{files: [{file_id, path, title, modified_time, content}, ...]}` (ingest; `content`
  optional — absent = non-text file, title/path-only row per the audit second-pass
  rule) → chunk + `replace_file` each, advance the watermark, `data: {synced,
  watermark}`. Malformed entries → ok:False actionable; empty ingest → ok:True no-op.
  TOOL_SPECS entry (tool 9/10) with the protocol in the description. Tests FIRST:
  plan-mode watermark; ingest chunks+stores+advances; non-text ingest stores one
  empty-body row; re-ingest of the same file is idempotent; malformed batch rejected.
  README tool line (9).

- [x] **T2.15 — `knowledge_search` tool + query map (D2)** —
  `handlers.knowledge_search` (`query` required non-empty; optional `limit` default
  3, capped at 10) → top chunks `[{file_id, path, title, heading}]`; the tool
  description says to confirm against the LIVE Drive original before quoting — the
  index is a finding aid. TOOL_SPECS entry (tool 10/10).
  `scripts/generate_agents_md.py` query map rewritten per proposal §3 (mission →
  Drive brief; questions → knowledge_search then live read; status → journal/ +
  checkins_by_date; tasks → kanban; who → member_list) + golden tests updated. Tests
  FIRST: tool missing (red); validation; ranking through the tool layer; golden
  render; malformed MATCH queries (raw sqlite3.OperationalError from the repo search
  primitive — T2.13 review) must surface as ok:False actionable summaries.

- [x] **T2.16 `DOC` — persona/skills knowledge guidance + coherence pass** —
  `prompts/persona.md`: knowledge rules (search the index, then read the live Drive
  original before quoting; the cache is an index, Drive is the record; agent-authored
  files are uploaded after writing); new `prompts/skills/knowledge/SKILL.md` (when to
  knowledge_search vs live read; how to run a knowledge_sync round; upload-after-write);
  README full pass (10 tools, v6 architecture, quickstart: allow.sh + seed placeholders
  + no rclone, status table); KICKOFF.md's "never touch proposal.md" line updated
  (proposal edits are task-sanctioned in v6). Acceptance: no bisync/rclone/
  digest_time/status_days references remain anywhere outside audit/history docs —
  INCLUDING README's old query-map line (search_files under docs/, T2.15 routing)
  and config.schema.json's drive_root "(rclone bisync root)" description (T2.15
  routing). Acceptance: make check green on the COMMITTED tree (T2.15 lesson: a
  working-tree-only change is not done).

- [ ] **T2.17 `DOC` `MANUAL-GATE` — `docs/verify/phase2.md` (v6)** *(gate doc written 2026-09-04, commit c772332; awaiting the human on the Pi — step 1 is the $GAPI STOP gate)* — full rewrite;
  FIRST item verifies `$GAPI` works in-container at our HERMES_REF (DM the agent: list
  the Drive root via the google-workspace skill) — **if it fails: STOP the phase and
  report to the owner; no workarounds.** Then: knowledge_sync incremental round-trip
  (first sync ingests; second sync is a no-op); knowledge_search diacritics proof on
  real data (`MATCH 'decision'` finds "decisión") + the agent confirms against the
  live file; FTS5 integrity-check command clean; UP path (journal entry uploaded,
  visible in the Drive browser); DOWN proof (edit a doc on Drive, next sync picks it
  up); digest end-to-end; AGENTS.md query map live; doc-extraction check ($GAPI
  export). No bisync/rclone steps remain. Leave unchecked for the human.

## Phase 2 follow-ups — v6.1 deterministic sync (owner-confirmed 2026-09-05)

> Mid-gate design change: the phase-2 gate caught the agent-mediated `knowledge_sync`
> inventing file contents (self-caught, repaired via the same-file_id-replaces
> contract) and stalling batch-shuttling 19 files through LLM context
> (docs/verify/phase2.md, step 2 evidence). D2 is amended (proposal §3/§11):
> **sync becomes a deterministic script; the tool is removed; file content never
> crosses LLM context** (AGENTS.md hard rule 11). Tasks run top-to-bottom in a
> dedicated session; T2.21 gates the remaining phase-2 boxes on them.

- [x] **T2.18 `scripts/sync_knowledge.py` — deterministic knowledge sync (D2 rev)** — TDD.
  Pure diff/plan logic in `src/coordinator/syncing.py` (watermark diff, changed-file
  selection — no I/O); I/O adapter in the script: list the knowledge Drive via the
  google-workspace skill CLI (`google_api.py drive search --raw-query`), download
  changed text files, ingest through `KnowledgeRepository` (same per-file reindex =
  DELETE + reINSERT semantics as the former tool); watermark stays derived
  (`MAX(modified_time)`). Flags: `--resync` (full rebuild), `--dry-run`. Idempotent:
  a second run with no Drive-side changes ingests 0 files. Tests: fake CLI transport
  (Protocol), watermark edge cases, non-text files title/path-only, no network.
  Acceptance: `make check`; on the Pi, a real round + a no-op second round.
  Notes: the freshness layering (read-through TTL gate, post-upload write-through,
  Changes-API cursor) is deliberately OUT of this task — T2.23/T2.24.
- [x] **T2.19 Wire the schedule: `make sync` + hermes cron** — `make sync` target
  wrapping the script via `uv run`; documented nightly `hermes cron` job (proposal §8.1
  runbook line + docker/README note; conversational creation is the operator path).
  Acceptance: `make sync` performs a real round on the Pi; `hermes cron list` shows
  the nightly job.
- [x] **T2.20 Remove the `knowledge_sync` tool (toolset 10 → 9)** — delete the
  TOOL_SPECS entry + `knowledge_sync` handler + its tests; rewrite
  `prompts/skills/knowledge/SKILL.md`'s sync section (cache refreshed by script/cron;
  the agent only reads) **and make the post-upload write-through mandatory in its
  upload section: after every successful `$GAPI drive upload`, run `make sync`**
  (proposal §3 freshness model — write-through only works if every writer uses the
  path); update README architecture lines, `config.schema.json`'s drive_root
  description, AGENTS.md tool counts. Acceptance: `make check`; the plugin
  registers exactly 9 tools; no live doc references the two-call flow (changelogs/
  gate history excepted).
- [ ] **T2.21 `DOC` — phase-2 gate refresh for v6.1** — update `docs/verify/phase2.md`
  pre-flight (9 tools; no `knowledge_sync`), re-verify step 2 (script round-trip +
  no-op) and step 3 (search diacritics on real data) against the script, remove the
  v6.1 resumption note, then run the remaining boxes (5–9) fresh.
  *(DOC refresh done 2026-09-05 — the gate doc is re-based on the deterministic script;
  the fresh gate run (pre-flight re-check, steps 2/3, boxes 5–9) awaits the operator on
  the Pi; the box ticks at sign-off together with T2.17.)*
- [x] **T2.22 `DOC`/`chore` — small template fixes logged during the gate** — gitignore
  `config/config.yaml` (any deployment leaves the tree clean); fix the gate's stale
  SQLite-version note (in-container 3.53.4, not 3.50.4). Acceptance: fresh-clone
  `git status` clean after setup.
- [x] **T2.23 Read-through freshness gate on `knowledge_search` (proposal §3)** — TDD.
  Before matching, the search path checks a last-freshness-check timestamp (injected
  `Clock`); older than `knowledge.freshness_ttl_minutes` (config knob, default ~10,
  schema + loader) → run the deterministic incremental sync (T2.18 engine) first, then
  proceed. Debounce: searches inside the TTL never re-check. Degraded mode: Drive
  unreachable → serve the cache and say so in the result summary (reading never
  hard-fails). Tests: TTL edge cases (clock injected), debounce, degraded mode,
  freshness round-trip (edit → gate → findable — the proper freshness test, not a
  single positive lookup). Acceptance: `make check`; on the Pi, a Drive-side edit
  becomes findable within one search after the TTL elapses.
- [ ] **T2.24 `OPTIONAL` — Drive Changes API cursor (proposal §3)** — replace the
  `modifiedTime` watermark with `changes.list`'s `pageToken` cursor (incremental
  change feed; `includeRemoved`) so deletions/trashes/moves are detected — the one
  thing a modifiedTime watermark structurally misses. Storage: a `sync_cursor` row in
  `settings`. Only after T2.18/T2.23 are stable; revisit if a real deletion leaves
  stale chunks that matter. Push webhooks (`changes.watch`) stay REJECTED: they need
  a public HTTPS receiver (no inbound ports by design).
- [x] **T2.25 `DOC` — prompt hardening from the phase-2 gate drift findings** —
  knowledge SKILL.md: add the auto-refresh-on-search line (T2.23 read gate, the
  runtime behavior the gate observed), an explicit "never read local kb_sync-style
  copies — they are not a designed artifact; read the LIVE Drive original", and
  "ids are internal — render paths/titles, never raw file_ids"; define the
  Drive-side journal destination (no `journal/` folder exists in the knowledge base,
  so the skill's "matching Drive folder" was unresolvable — the bot uploaded to the
  root; convention established in-gate: `journal/` on Drive). Surface the template
  decision on installing `prompts/skills/*` into the runtime skill store (nothing
  installs them today; Hermes' skill curator filled the gap with an agent-authored
  skill that misdirected the bot — see phase2.md deviations 2026-09-05) — owner call
  on the mechanism (setup.sh install vs curated). Salvage verification note from the
  removed runtime skill: does `cronjob action: update` really require `job_id`
  (our relays emit `action: edit` + `name`; name-based edit verified at this pin).
  *(Done 2026-09-06 — owner call: setup.sh install; new step 4/7 copies
  `prompts/skills/*/SKILL.md` → `$HERMES_HOME/skills/coordinator-<name>/SKILL.md`,
  overwrite every run; Hermes-style frontmatter on all four skills;
  `schedules.md` → `schedules/SKILL.md` for the uniform glob. Review: no hard
  violations after one fix round; Notes log carries the judgement calls and the
  record-only salvage note.)*
- [x] **T2.26 `fix` — Google-token permissions + in-container one-shot ergonomics**
  *(surfaced by the phase-2 gate 2026-09-05)* — the token file
  (`~/.hermes/google_token.json`) ended up root-owned mid-run, breaking every
  refresh write by the runtime uid (the agent improvised a shadow-HERMES_HOME
  workaround — exactly the drift class the template must prevent). (a)
  `scripts/sync_knowledge.py` pre-flight: if the token path (where determinable)
  exists but is not writable by the effective uid, fail LOUD with the exact
  chown/chmod remedy instead of a confusing CLI auth error; (b) `scripts/setup.sh`:
  if the token exists and is not writable by the container uid, print (or repair —
  the script runs on the host with operator privileges) the fix; (c) gate doc step 1
  gains "run the OAuth setup as the container's runtime user, never root/sudo";
  (d) pin the correct in-container one-shot invocation — dual interpreter (Hermes
  venv for the coordinator imports, gapi venv for the CLI); the T2.23 freshness
  gate's subprocess already resolves this, reuse its form, and put the line in the
  knowledge SKILL.md (may absorb into T2.25); (e) runbook adjacency: token-
  permissions troubleshooting lands with T4.2. (f) ROOT CAUSE of the root-owned
  token found in-gate: `docker compose exec` defaults to root in this container —
  document the `--user 1000` requirement for any token-writing command (OAuth,
  checks), add the guard to setup.sh's printout, and evaluate pinning the exec
  user at the compose level. Tests: transport pre-flight rejects
  an unwritable token (chmod in tmp_path); setup.sh smoke asserts the check line.
  *(Done 2026-09-06 — both review axes and both fix-delta re-reviews clean, fixed
  point 40b137e. The spec's "dual interpreter" parenthetical superseded on
  empirical evidence: the container's default `python3` IS the Hermes venv and
  image-ships googleapiclient — the pinned one-shot is the single-interpreter
  T2.23 subprocess form, the split forbidden. Compose-level exec-user verdict:
  not implementable (docker-compose.yml comment + Notes). T4.2 pointer added.
  Judgement calls, residuals and Pi evidence in the Notes log.)*

## Phase 3 — Persona

- [ ] **T3.1 Persona toggle** — `persona.enabled: true` in schema + config loader;
  `setup.sh` installs SOUL.md only when enabled. Tests: schema accepts/rejects type;
  loader exposes flag; smoke test asserts dry-run respects it.

- [ ] **T3.2 `DOC` — `prompts/triage.md`** — severity rubric (urgent/high/medium/low)
  adapted from OpenExecutive `triage_prompt.py`; channels remapped to group post / owner
  DM / journal note only; "when in doubt, lower severity" rule kept.

- [ ] **T3.3 `DOC` — `THIRD_PARTY_NOTICES.md`** — Apache 2.0 attribution:
  source repo `SenteLabsAI/OpenExecutive`, files taken (`prompts/executive_persona.py`,
  `prompts/triage_prompt.py`), local modifications statement, license text reference.

## Phase 4 — Hardening

- [ ] **T4.1 `scripts/backup.sh`** — tar.gz of `data/project/` + `hermes-coord.db` +
  kanban DB (path via `HERMES_HOME`) into `backups/YYYY-MM-DD-HHMM.tar.gz`; retention 7;
  no Drive copy step (v6: Drive holds the record docs; the knowledge cache inside
  hermes-coord.db is rebuildable from Drive — backing it up is convenience, not
  necessity); `--dry-run`. Tests: `tests/test_backup_smoke.py` —
  `bash -n`; dry-run writes nothing.

- [ ] **T4.2 `DOC` — `docs/runbooks/`** — `restore-backup.md`, `new-member.md`
  (proposal §8.2 v6 door-first flow — `allow.sh` + the sender's ID completing the row —
  verbatim steps), `dst-resync.md` (the twice-a-year conversation), and the
  token-permissions troubleshooting runbook deferred from T2.26 (root-owned
  `google_token.json`: the chown/chmod remedy, the `docker compose exec --user`
  guard, setup.sh step 8/8, the sync script's pre-flight). (The v5
  `bisync-recovery.md` runbook is gone with bisync — D4.)

- [ ] **T4.3 `DOC` `MANUAL-GATE` — `docs/verify/phase4.md`** — backup restore drill
  executed and signed off; backup scheduled (host cron or compose service); cost screenshot
  from OpenRouter console vs $20 cap.

## Phase 5 — Escalation (deferred — do NOT implement)

- [ ] **T5.1 `DOC` — `docs/spikes/vector-rag.md`** — feasibility notes only (sqlite-vec
  aarch64 wheels, embedding model options, integration point = coordinator toolset).
  Trigger criteria documented (e.g., `search_files` answers degrade or corpus > ~500
  files). Implementation forbidden until the team decides.

---

## Notes log

*(agents append here: date, task, deviation/observation)*

- 2026-09-06 T2.26 — Google-token permissions + in-container one-shot ergonomics.
  Review verdict: no hard violations after one fix round (two fresh axes, fixed
  point 40b137e; both fix-delta re-reviews clean — 0 unresolved). The round: the
  compose exec-user evaluation verdict got its durable artifact (the Spec axis's
  one hard finding, on (f)) — docker-compose.yml now records it; T4.2's block
  gained the deferred-runbook pointer (Spec (e) recommendation); setup.sh warns on
  root-with-HERMES_UID-unset and on non-numeric HERMES_UID/HERMES_GID (the latter
  previously aborted the whole script in set -u arithmetic — reproduced, after
  steps 1-7 had already applied); tests root-proofed (explicit euid/egid
  injection; skipif(root) where the scripted root branch legitimately differs).
  Arbitrations: (1) the spec's "dual interpreter (Hermes venv for the coordinator
  imports, gapi venv for the CLI)" parenthetical was the gate's disproved
  hypothesis — verified empirically in-container (read-only, uid 1000): the
  default `python3` IS the Hermes venv (/opt/hermes/.venv/bin/python3, first on
  PATH) and image-ships googleapiclient (site-packages dated the 2026-09-02
  build day); /usr/bin/python3 (same 3.13.5) lacks it; the bot-created
  /opt/data/venvs/gapi (2026-09-05 19:54Z) duplicates what the image provides.
  The operative spec clause — "reuse the T2.23 freshness-gate subprocess form" —
  is single-interpreter ([sys.executable, script_path], hermes_plugin.py), and the
  documented one-shot with the container's default python3 was re-verified live
  (dry-run, exit 0, real Drive listing). The skill/gate/docstring pins therefore
  state ONE interpreter and forbid the split. (2) Compose-level exec-user verdict
  ((f) evaluation): NOT implementable — the compose spec has no exec-user key
  (exec inherits the container's user; root here because the upstream s6
  entrypoint must start as root to drop to HERMES_UID/GID), and a service-level
  `user:` would break that privilege drop. Guard = documentation (setup.sh 8/8
  printout) + the 8/8 check + the sync pre-flight; no implementation, per the
  task's "implementation only if owner approves" and the negative verdict.
  Judgement calls: (a) the sync pre-flight predicates on the process euid (no
  HERMES_UID knob) and fires for --dry-run too — deliberate: a dry-run still
  lists via the CLI, which can trigger a token refresh write; on divergent
  topologies (operator uid ≠ container uid) setup.sh 8/8 is the parameterized
  backstop; (b) the bash/python writability arithmetic exists twice (lockstep
  debt recorded; mode rendering aligned to bare octal on both sides; no
  cross-language equivalence test); (c) supplementary groups are not consulted in
  either implementation (the real shapes are owner-euid or root-owned — the
  incident class); (d) the dry-run-with-broken-token smoke test remains
  root-fragile (recorded per the Standards delta reviewer; same skipif pattern
  would cover it in a later round); (e) out-of-scope one-shot references left
  untouched (proposal.md §8.1 and docker/README.md still carry the bare one-shot
  without the interpreter pin — neither is a named file of this task); (f) the
  T2.25 note's historical "[n/7]" text stays as history after the /8 renumber.
  Residual hazard recorded, NOT actioned (rule 8): the host-side docker transport
  (GapiCliTransport._run — the `make sync` path) execs the CLI WITHOUT --user, so
  a mid-run token refresh from that path can still root-own the token; the
  pre-flight converts the NEXT run into the loud remedy, and the nightly
  in-container cron runs as uid 1000 — a --user-aware transport is a possible
  small follow-up.
  Pi follow-through: git pull + `uv run ./scripts/setup.sh` (env keys exported by
  grep — the dead `[gdrive]` stanza still breaks `source .env`). **ACCEPTANCE
  (agent-run over SSH, 2026-09-06):** Pi at e4499e3; step 8/8 on the live token →
  "ok: owner 1000, mode 600 — writable by uid 1000" + the exec-user NOTE printing
  `docker compose exec --user 1000 gateway <command>`; the updated knowledge
  SKILL.md landed at ~/.hermes/skills/coordinator-knowledge/SKILL.md
  byte-identical (diff clean); container untouched (Up 21h — no restart, as
  designed). Runtime acceptance of the changed one-shot line (the bot running
  the pinned interpreter form after an upload) rides the gate's step-6 digest run.

- 2026-09-06 T2.25 — prompt hardening + template-owned skills install. Review verdict:
  no hard violations after one fix round (two fresh axes, fixed point 00e3f9ee; fix
  deltas re-reviewed clean on both). The round: (1) completed the /7 renumber — the
  [1/6]/[2/6] banners survived the first pass, contradicting the renumbered docstring
  (hard finding on BOTH axes; regression pin added: every `[n/7]` banner asserted in
  the dry-run plan); (2) hardened the knowledge skill's journal wording from "that
  folder exists" to "the designed destination; if that folder is missing, create it
  there first" — fresh deployments get no false premise (Spec-axis low finding; on
  the live Pi `journal/` was operator-created in-gate, gate record + owner facts).
  Arbitrations: rule-2 atomicity question (commit names two work items) dissolved on
  the spec — T2.25 is ONE ROADMAP task whose block names both the install mechanism
  and the four knowledge additions; the `schedules.md` → `schedules/SKILL.md` move is
  the enabling change implied by the owner's `prompts/skills/*/SKILL.md` glob (same
  ruling class as T2.19's mount). Judgement calls: (a) the `coordinator-<name>`
  mapping lives in three places (setup.sh dest, each frontmatter `name:`, test
  helper) — pinned together by the frontmatter test, no single-sourcing at this
  scale (extraction would be Speculative Generality, per the Standards nit on the
  same shape for the step total "7"); (b) test-side `subprocess.run(...)` duplication
  (5×) recorded, not extracted — pre-existing pattern, rule 8; (c) skill elaborations
  ("never run a manual sync just to read", "relay that in your reply", "never drop
  journal files in the Drive root") each track a runtime fact (the T2.23 read gate
  announces refreshes via the result summary; the gate's root-upload drift); (d) the
  auto-refresh line documents the owner-shipped T2.23 design, adds no behavior.
  Frontmatter format pinned from observed runtime evidence: the removed curator
  skill's own header (`name`/`description`/`category: productivity`, name = dir name)
  read from its Pi backup; top-level `skills/<dir>/` loading proven at this pin by
  that skill and `kanban-management`. RECORD-ONLY, pending verification, never to be
  tested against the live bot: the removed curator skill claimed `cronjob action:
  update` requires `job_id` — our relays emit `action: edit` + `name`, and name-based
  edit was verified at this pin in the phase-1 upstream evaluation; check it only if
  a real cron edit ever fails. Pi follow-through: git pull + `uv run ./scripts/setup.sh`
  (env keys exported by grep — the dead `[gdrive]` stanza still breaks `source .env`,
  recorded 2026-09-05); fresh-DM skills ask appended below as acceptance evidence.
  **ACCEPTANCE (runtime evidence, 2026-09-06):** fresh DM session, operator asked the
  bot for its skills → the reply lists ALL FOUR template skills by exact name, grouped
  first under "Coordinación del equipo (mi pan de cada día)", with trigger
  descriptions mirroring the installed frontmatter (gist, verbatim):
  "- coordinator-check-in — cuando abre la ventana de check-in de un miembro: hago el
  saludo, registro done/next/blockers. / - coordinator-digest — el job de las 17:00:
  convierte los check-ins del día en entrada de journal + resumen al grupo. /
  - coordinator-schedules — cuando pides recalcular horarios de check-in (regla: los
  cron_relay se pasan verbatim, nunca recalculo zonas horarias). /
  - coordinator-knowledge — preguntas que necesitan docs del equipo: busco en el
  cache y confirmo contra Drive." The schedules reply even carried the skill body's
  verbatim-relay rule, and the removed `coordinator-operations` stays absent (the
  install did not resurrect drift). Loader accepted the top-level `skills/coordinator-*/`
  dirs from the ~/.hermes volume with zero restart — owner decision 3 verified live.
  Post-acceptance episode (same day, SECOND fabrication instance): asked about the
  day's check-in, the bot stated the designed digest flow correctly (the loaded
  skills at work) but appended a "known cache bug" diagnosis — script missing /
  cron failing (both verified false, agent-run read-only: script present, cron last
  run 2026-09-06T03:30:14Z ok, job history = fail 13:00:55 → ok 13:02:52 → ok
  03:30:14) plus an "estabas arreglando por fuera" attribution that is real memory
  of the earlier session's closing agreement, re-read through the poisoned premise
  (corrected on operator-provided context; NOT invention). The episode-1 prompt even
  cited the SUCCESSFUL 13:02:52 recovery log as a failure. Recorded with full
  verdict in docs/verify/phase2.md deviations — NOT a T2.25 failure (the acceptance
  evidence stands; the confabulated-diagnosis class is distinct from the
  missing-guidance class this task fixed); owner call pending on prompt-level
  "verify live state before asserting failures" hardening.

- 2026-09-05 T2.21 — DOC part done (gate-run part pending on the Pi; box stays unticked
  per the T2.17 MANUAL-GATE pattern). docs/verify/phase2.md re-based on the v6.1 world:
  resumption note removed (its promised effects applied inline); "what shipped" covers
  T2.18–T2.23; pre-flight gains the 9-tool re-check (git pull + up -d first — bind
  mounts apply only at container creation); step 2 retitled for the deterministic
  script with two new boxes (first round ingests / second round no-op) and the
  agent-mediated run kept as the labeled incident record; step 3 documents the T2.23
  read path (TTL refresh + degraded mode); steps 5/6/9 carry the mandatory
  write-through and the script-based DOWN proof; two T2.22-resolved deviations
  annotated; closing note ticks T2.17 + T2.21 at sign-off; drive_root wiring follow-up
  restated against the script. Docs-only — no tests touched. Remaining: the fresh gate
  run with the operator (pre-flight re-check, steps 2/3 re-verification, boxes 5–9).
- 2026-09-05 T2.16 — DOC task executed directly (commit 7312709); the checkbox stayed
  unticked because the v6.1 mid-gate re-queue landed hours later and gated the remaining
  phase-2 boxes on T2.18–T2.20 (which have since shipped). Close-out re-verified the
  acceptance at HEAD 5601f57: persona hard rule 6 + self-review checklist;
  prompts/skills/knowledge/SKILL.md (its sync section carries the v6.1 script flow via
  T2.20); README quickstart (allow.sh door, null seed IDs, no rclone) + status table +
  9-tool architecture lines; KICKOFF.md proposal rule; schema drive_root description
  de-rclone'd; sweep clean — no live bisync/rclone/digest_time/status_days/search_files
  references outside rejection tests, explanatory comments, and history docs; make check
  green on the committed tree (378 tests). Residue recorded (owner call, not actioned):
  README Requirements still offers "a Google account that can reach the team's Shared
  Drive folder" while the gate's Drive-scoping deviation and the schema v6.1 description
  put the knowledge base on the bot account's own My Drive; `project.drive_root` remains
  parsed-but-unused (tracked in the gate deviations).
- 2026-09-05 T2.22 — DOC/chore (no review protocol required). config/config.yaml
  gitignored; Pi acceptance: after pull, git status shows only the operator's own
  .env.bak-20260905-095236 backup artifact (operator file to remove, not a template
  concern; git check-ignore confirms the config line). Gate SQLite note fixed in
  docs/verify/phase2.md — the rank-form requirement stands on the suite's 3.50.4 AND
  the container's 3.53.4 (T2.21's rewrite inherits the corrected text).
- 2026-09-05 T2.23 — review verdict: no hard violations (both axes, fixed point
  c8d0d78; fix-delta re-review clean). Arbitrations applied: (1) the degraded path
  widened to the WHOLE freshness seam — a raising last_check (e.g. sqlite lock while
  reading the stamp) degrades the round instead of failing the read; no stamp is
  written on a failed read (correct: the debounce stamp guards the Drive sync, and a
  stamp would falsely mark the cache checked under the same lock); (2) the gate's
  report fallback anchored to the "ingested" line — anything else yields the fixed
  "no report output", so nothing unanchored reaches the LLM summary. Judgement calls:
  (1) the gate stamps via raw settings-table SQL, not SettingsRepo (the stamp key is
  deliberately outside repositories.DEFAULTS so setting_get/set cannot touch it); a
  settings-layer change would only err toward an extra refresh — benign; (2)
  timeout_seconds constructor knob is beyond spec but pins the bounded-subprocess
  contract at its default; (3) the ImportError guard around the lazy config import is
  defensive for container venvs without jsonschema/PyYAML; (4) two gateway threads
  due simultaneously both refresh — a double sync, not a correctness break (upsert
  stamp idempotent, sync idempotent, WAL absorbs the second connection); (5) the
  compose ./config:/opt/data/config:ro mount is the enabling change that makes the
  config knob real in-container (without it the knob lives in a file the container
  never sees) — same ruling as T2.19's script mount. Acceptance evidence (Pi): gate
  wired at ttl 10 (operator config.yaml predates the knob → loader default, backwards
  compat proven live); Drive-side upload → debounced search MISSES it (0 hits);
  stamp backdated 11 min → the very next search runs the sync subprocess and the
  edit is in that search's results; following search debounces. Cache left
  consistent via trash + --resync purge.
- 2026-09-05 T2.20 — review verdict: no hard violations (both axes, fixed point
  deb1f1e). Arbitrations applied: (1) the deleted B5 "watermark advances" half is
  pinned literally again (empty + whitespace script tests assert repo.watermark());
  (2) canonical_modified_time's docstring refreshed — the raw-store fallback phrasing
  described no production path since the T2.18 fix round (T2.18 note 6's "until
  T2.20"); (3) stale "seven_tools" test names in the touched manifest test renamed.
  Ruling recorded, NOT actioned: proposal.md §1 still carries the v6-era 10-tool /
  knowledge_sync text (lines 64/125-127; §10 line 585 is changelog history) — proposal
  is not a named file of this task and DoD forbids unsanctioned proposal edits; §11
  already documents the 10 → 9 change. Owner call whether to amend §1 in a docs task.
  Acceptance evidence: make check green (350 tests); in-container census on the Pi
  (after git pull + docker compose up -d) prints 9 tools, knowledge_sync absent;
  two-call scan of prompts/, README.md, AGENTS.md, src/, scripts/, config/ clean.
- 2026-09-05 T2.19 — review verdict: no hard violations (spec axis, fixed point dce8afa).
  Judgement calls: (1) the compose file-level mount
  ./scripts/sync_knowledge.py:/opt/data/scripts/sync_knowledge.py:ro is beyond the
  literal task text but is the ENABLING change for the documented job — hermes cron
  --script resolves under ~/.hermes/scripts/, so without the mount the nightly job
  fails every run; (2) the §8.1 conversational phrasing names the raw command without
  encoding --script/--no-agent — a literal-minded bot could create an agent-mediated
  job; mitigated by the same line's "no LLM in the loop" + the CLI equivalent.
  Acceptance evidence (Pi): make sync performs a real round (no-op: 19 unchanged @
  watermark 2026-09-05T09:25:35Z, exit 0); the job was created via the documented CLI
  (hermes cron create '30 3 * * *' --name knowledge-sync --script sync_knowledge.py
  --no-agent); hermes cron list shows it; a forced hermes cron run SUCCEEDED and
  delivered the script's metadata-only report verbatim (no LLM invoked). Ops note: the
  first forced run FAILED with "Script not found" until docker compose up -d recreated
  the container with the new mount — bind mounts apply only at container creation
  (incident acked).
- 2026-09-05 T2.18 — review verdict: no hard violations (two fresh axes, fixed point
  44ae92f; delta re-reviews clean). Fix round arbitrated UP one [JUDGEMENT] flagged
  independently by both axes: unparseable modifiedTime is now SKIPPED in every mode — a
  resync storing the raw string would make it the BINARY-MAX watermark and permanently
  break the no-op second run; the T2.14-era raw-store fallback dies with the legacy
  handler (T2.20). Judgement calls: (1) _wipe_cache hand-writes the FTS-linkage DELETEs
  in the script; a KnowledgeRepo.wipe() would single-source the invariant
  (repositories.py not a named module this task); (2) real rounds print the ACTUAL
  post-round watermark (repo.watermark()), not the plan projection — a failed download
  must not overstate the cache; (3) --transport/--gapi-path flags beyond the two spec'd
  flags — needed for the three run targets (dev uv / Pi-host docker / in-container
  direct); (4) page-cap (>200 children) and BFS depth-cap (>10) truncations warn on
  stderr but are not paginateable with the CLI's fixed page size — a >200-children
  folder needs CLI pagination (out of scope, recorded); (5) sub-second race: an edit
  landing in the watermark's second after the listing is missed (<1 s window; T2.24
  Changes-API class); (6) canonical_modified_time's docstring keeps the T2.14 "kept
  visible in the cache" phrasing — accurate for the function passthrough (legacy
  handler path until T2.20), not for plan_sync; (7) multi-paragraph docstrings follow
  repo convention despite rule 6's "one-line" letter; (8) is_text_mime is text/* only —
  JSON/YAML/etc index title/path-only by design; (9) concurrent syncs get unique mkdtemp
  workspaces; HERMES_UID misconfiguration fails loudly (SyncError) and stale temp dirs
  are gitignored. Pi acceptance (found and fixed two real transport bugs): (a) downloads
  must live in the compose-mounted data dir — the docker-transport CLI writes
  in-container while the host script reads on the host; the transport now declares its
  download workspace and maps host paths to /opt/data/workspace (29dd091); (b) CLI
  stdin detached — exec -T consumed the calling shell's remaining input (1775fda);
  mapper pinned by unit tests (e1e9a2e). Acceptance evidence: baseline round 19
  unchanged @ watermark 2026-09-05T09:25:35Z (matches the gate record); Drive upload →
  round ingested exactly 1 (watermark → 12:41:02Z) → second round NO-OP (20 unchanged,
  0 ingested) → --dry-run plan-only; Drive trash → --resync purged the deleted file
  (19 files rebuilt through the plugin chunker, 0 failed) → final round no-op. Cache
  consistent with Drive; the agent-mediated tool flow untouched (T2.20 removes it).
- 2026-08-29 T0.1 — review verdict: no hard violations; judgement calls recorded: (1) version
  string lives in both pyproject.toml and src/coordinator/__init__.py and the smoke test
  asserts only str/non-empty (drift would pass) — consider hatch dynamic version or an
  equality assert in a later task; (2) [tool.ruff] extend-exclude=["*.md"] is a no-op
  (ruff only lints Python) — harmless, may be dropped later; (3) .gitignore adds
  .pytest_cache/.mypy_cache/.ruff_cache beyond the spec list (orchestrator-directed:
  caches must never be committed); (4) smoke test asserts isinstance(str)+truthy rather
  than == pyproject version (covered by (1)); license uses file= form, PEP 639 SPDX
  string is cosmetic alternative.
- 2026-08-29 T0.2 — review verdict: no hard violations; judgement calls recorded:
  (1) ci.yml's run chain "uv run make lint && make type && make test" is pinned in three
  places (ci.yml, Makefile composition, test constant) — spec-driven, tolerated;
  (2) python-version "3.11" pin in setup-uv duplicates .python-version (which setup-uv
  reads natively) and test_ci.py hard-asserts it — drift risk on a future Python bump;
  (3) validate step (python -m coordinator.config validate) is intentionally red until
  T0.3/T0.4 land those files — roadmap-sequencing artifact, repo has no remote yet;
  (4) test reads a repo-tree file (ci.yml) though AGENTS.md tests note says "tmp paths
  only" — read-only/deterministic/required by acceptance, read as secrets/IO-safety
  clause; (5) workflow[True] key name (PyYAML YAML-1.1 quirk) is commented inline;
  (6) orchestrator protocol marks checkboxes in a post-review chore commit, so rule 2's
  "update checkbox" lands separately from the feat commit by design.
- 2026-08-29 T0.3 — review verdict: no hard violations (both axes); judgement calls:
  (1) timezone pattern is an intentional two-segment approximation — rejects real zones
  like America/Argentina/Buenos_Aires, Etc/UTC, US/Hawaii, bare UTC and accepts junk like
  Xx/Yy; spec wording "IANA-looking Area/City" supports it; authoritative check is T0.4's
  zoneinfo probe; (2) micro-constraints beyond spec: minLength:1 on five strings,
  minimum:1 bans chunk_size 0 (not just negatives), additionalProperties:false nested
  (spec arguably root-only) — all tighten, none loosen; (3) log_level is a bare string
  (no minLength/enum) — empty string validates; (4) budget-key rationale duplicated in
  example yaml + schema description; (5) cosmetic: redundant deepcopy in test, three
  schema JSON lines exceed 100 cols (ruff doesn't lint JSON).
- 2026-08-29 housekeeping — .DS_Store added to .gitignore (macOS artifact appeared
  untracked; prevent accidental commit).
- 2026-08-29 T0.4 — review verdict: no hard violations; one hard-leaning item arbitrated
  as judgement: CLI main() lives in config.py (rule 3 pure/impure) — kept-and-documented
  because T0.2's committed CI seam pins `python -m coordinator.config validate` and the
  AGENTS.md repo map assigns config loading to config.py; moving it would break ci.yml.
  Other judgement calls: (1) subparser machinery for one command + optional schema_path
  param with sibling-discovery default — mild YAGNI creep, test-justified; (2)
  iter_errors loop always raises on first error — next(iter()) would state intent;
  (3) two happy-path tests read committed example yaml instead of tmp_path (read-only,
  deterministic, precedent tolerated); (4) minor test duplication/indirection
  (inlined mutant, dict keyed by callables); (5) # type: ignore[import-untyped] on
  jsonschema/PyYAML imports (no py.typed, stubs out of scope) — future chore candidate:
  add types-PyYAML/types-jsonschema to dev deps and drop the ignores.
- 2026-08-29 T0.5 — review verdict: no hard violations (both axes; DDL byte-identical to
  proposal §1, migration atomicity empirically verified, no false WAL pass). Arbitrated:
  migrate(conn, applied_at: str) deviates from the sketched migrate(conn) — required
  caller-supplied timestamp is the strictest rule-5-compliant time injection; specced
  single-arg call intentionally raises; docstring should state caller owns the timestamp.
  Judgement calls: (1) migrate() silently commits any caller-open transaction
  (executescript implicit-COMMIT hazard; safe in startup-only wiring — docstring should
  disclose); (2) unreachable None branch on journal_mode pragma row (candidate for
  deletion); (3) schema.sql read at module import (fails at import, not first use);
  (4) test fixture duplication (connect+try/finally repeated); (5) row_factory=Row and
  DatabaseError are unrequested but sanctioned API decisions (row_factory was explicitly
  offered as decide-and-note in the seam).
- 2026-08-29 T0.6 — review verdict: no hard violations (both axes; semantics verified in
  SQL, Protocol signatures byte-identical to concretes). Judgement calls: (1) ORDER BY
  name uses SQLite BINARY collation — case-sensitive ("alice" after "Zed"); spec doesn't
  mandate collation, tests use uniform capitalization (latent trap); (2) update() None =
  leave-unchanged, so nullable columns cannot be reset to NULL — T0.8 must decide
  explicit-null/sentinel if clearing wake/role is ever needed; (3) Protocol⇄concrete
  docstring/signature echo is PEP-544-required but unpinned — conformance test candidate;
  schema defaults live in 3 places (schema.sql/Protocol/concrete); (4) update's 7 guard
  blocks are explicit by design (loop would collapse them); (5) `next` param shadows the
  builtin — required by the proposal's column name; (6) active:int not range-checked in
  this layer (update(active=7) stores silently) — validation belongs to handlers (T0.8);
  (7) minor test duplication + assert-narrowings vanish under -O.
- 2026-08-29 T0.6 sanctioned addition — CheckinsRepo.by_date(date) ordered by member_id:
  minimal read required by the task's CRUD round-trip tests and T0.8/T1.3 digest flow.
- 2026-08-29 T0.7 — review verdict: no hard violations; spec axis independently recomputed
  all zoneinfo ground truth (Berlin flips exactly at 2026-03-29T01:00Z and
  2026-10-25T01:00Z; module matches to the second; adversarial +13/−9/+05:45 wakes and
  wraparounds all match an independent recompute). Judgement calls: (1) minute mapping
  uses floor division — would misround only sub-minute historical LMT offsets (modern
  zones whole-minute, out of scope); (2) duplicated bad-wake parametrize literals in
  tests; (3) zone-lookup wrap pattern echoed in config.py vs scheduling.py (different
  concerns, not merged). Any timezone-aware at_utc accepted (not just UTC-constructed) —
  documented contract.
- 2026-08-29 T0.8 — review verdict: 1 hard finding, FIXED in fix commit 6b86ba8 (delta
  re-reviewed: FIXED, no regressions): re-deactivating an already-inactive member re-emitted
  the pause relay, breaching the contract's "cron_relay present when a schedule changed";
  fix diffs active against the stored row; combined wake+deactivate on already-inactive
  now emits no relay (wake still persisted; suppression documented in handler comment).
  Judgement calls: (1) tz-only change emits an edit relay (schedule depends on tz) —
  beyond the literal "edit on wake change", kept; (2) wake edit on an INACTIVE member
  emits an edit relay for a paused job — literal-spec, kept; reactivate-after-pause
  produces no relay (no resume action in this task) — future need; (3) member_list
  defaults to active-only and never exposes telegram_id (DM privacy); (4) digest-job
  relay for setting_set deliberately absent from T0.8 — future need; (5) uniform 5-param
  handler signature leaves unused deps in some handlers (T0.9 mechanical dispatch);
  (6) payload casts (~15) would dissolve into a validate-into-typed-dataclass refactor;
  (7) _timezone_error duplicates scheduling's except-tuple — shared validate_timezone
  helper is a refactor candidate; (8) _missing_required call in member_add is dead weight
  (deletable); (9) "remains paused" summary nit when the member never had a job.
- 2026-08-29 T1.5 — review verdict: no hard violations (spec axis re-verified all pinned
  literals with fresh zoneinfo; both axes confirm real-stack composition, only the clock
  faked). First-run PASS of the integration suite (legitimate: stack was TDD'd task by
  task). Judgement calls: (1) "a full day" is 4 independent tests each reseeding a fresh
  tmp DB (step-wise, not one continuous flow — documented in the test docstring);
  (2) nudge_limit set beyond the named digest settings (harmless); (3) away-and-back wake
  pairs as relay-producing mechanics (roster stays at seeded 4); (4) relay_schedule
  helper walks the contract nesting with isinstance asserts.
- 2026-08-29 T0.14 — review verdict: no hard violations (spec axis verified upstream
  fidelity live: gateway command byte-identical to upstream compose at pin 5fc308a7,
  mounts match README exactly, HERMES_REF matches build URL byte-for-byte, secrets render
  null with no .env present). Judgement calls: (1) ~/.hermes:/opt/data volume added
  beyond the two named mounts — upstream's own gateway does exactly this; container would
  be stateless without it; (2) build implemented as git-URL #ref fragment instead of
  build arg — upstream Dockerfile takes no ref ARG (documented in docker/README.md);
  (3) ./data mounts at /opt/data/workspace (cannot overlay the HERMES_HOME volume);
  (4) plugin mount read-only; (5) PASSTHROUGH_VARS list in the test re-lists compose's
  env block without a set-equality drift guard — new env var could leak into rendered
  config undetected (add set-equality test later); (6) the pin SHA lives in 3 places
  (HERMES_REF, build URL, README copy) with the README copy hand-updated — drift risk;
  (7) docker-in-tests is spec-sanctioned with clean skip; (8) working_dir for AGENTS.md
  injection deliberately deferred to a later phase.
- 2026-08-29 T0.13 — review verdict: no hard violations (spec axis probed all seven
  steps incl. 0600 perms on rclone.conf and zero writes under dry-run). Judgement calls:
  (1) bare `python` for the config-validate delegation (spec-literal) vs the repo's
  `uv run` convention — needs the coordinator venv on PATH; candidate: `uv run python`;
  (2) heredoc interpolates the refresh token unquoted into rclone's token JSON — a token
  containing quotes/backslashes would silently corrupt the conf (rclone tokens are
  typically URL-safe; low risk); (3) RCLONE_CONFIG_PATH override knob is mild YAGNI;
  (4) hermes config set triple appears 3x (dry-run echo, fallback block, real calls) —
  drift risk; (5) dry-run stanza hand-copied rather than derived from one builder;
  (6) test REQUIRED_ENV mirrors the script REQUIRED_KEYS list (drift would shrink the
  no-leak scan); (7) env-keys-from-environment reading (compose loads .env at runtime)
  — a source-.env-if-present convenience was considered and left out (contract change).
- 2026-08-29 T0.12 — review verdict: no hard violations (spec axis probed: byte-identical
  reruns incl. mixed-case names, clean overwrite, folder tree matches proposal §3
  entry-for-entry, telegram_id never rendered). Judgement calls: (1) roster ordering is
  case-sensitive (BINARY collation — same known T0.6 note); (2) render() lives in
  scripts/ outside mypy-strict scope (tests importlib-load the script); (3) hardcoded
  example date 2026-08-28 in the static structure block (deterministic but stale-prone
  prose); (4) golden pattern triples the static body (independent-source-of-truth
  intended; GOLDEN_EMPTY could derive from GOLDEN); (5) EXPECTED_* loops re-assert what
  golden equality already pins (dual maintenance on wording edits); (6) empty-roster
  placeholder + unreadable-DB error-path test beyond spec (defensible).
- 2026-08-29 T0.10 — review verdict: no hard violations (both axes; CLI probed end-to-end:
  nested mkdir, seed-once marker, all-or-nothing with no marker on failure). Judgement
  calls: (1) _db_telegram_id_conflicts issues member-table SQL directly in the script —
  second owner of member SQL; a repo method (filter_existing_telegram_ids) is the fix
  candidate; (2) seed-once probe depends on seeded_at staying OUT of repositories.DEFAULTS
  (get() fallback would silently always-skip) — comment documents the trap; (3) hardening
  beyond spec (dup-in-seed, type validation, DB-conflict check + 4th test) logged per
  rule 8; (4) migration failures print under the "seeding failed" prefix (misleading);
  (5) empty seed file stamps the marker (spec-consistent); (6) rerun with a moved/typo'd
  --seed path exits 0 silently (marker short-circuits before file read); (7) two loose
  test assertions (stderr != "", "seeded_at" in stdout).
- 2026-08-29 T0.10 ops note — first dispatch attempt (7b96ba2e) failed pre-work (agent
  died after skill load; zero repo changes); fresh retry (8e60459e) completed cleanly.
- 2026-08-29 T0.9 — review verdict: no hard violations (spec axis probed: zero schema
  drift both directions vs handlers, dep order correct, identity passthrough, module
  imports with Hermes absent). Design note: "import of Hermes guarded" implemented as
  ZERO Hermes import + HermesContext Protocol — the stronger structural form; no
  try/except ImportError exists (nothing to guard). Judgement calls: (1) payload field
  sets live in 3 places (handlers._*_FIELDS, TOOL_SPECS schemas, test EXPECTED_FIELDS) —
  drift is pinned by tests; a single-source refactor candidate; (2) register() returns
  list[str] of names (spec silent; no consumer yet); (3) the 4-dep kwargs clump repeats
  across dispatch tests (helper absorbs partially); (4) "hermes" not in sys.modules
  assertion is suite-order dependent if a real hermes package ever enters the venv.
- 2026-08-29 housekeeping (user-directed, between T1.6 and the gate run) — repo renamed
  hermes-setup → finki-coordinator (gh rename; old URL redirects); README.md expanded from
  the T0.1 stub to full quickstart/architecture/status. Content kept to shipped phases
  only (no promises beyond ROADMAP); Pi clone remote updated; no production code touched.
  Off-queue by user direction (T0.1's "full usage docs land with a later task" was never
  queued — tracked here instead). 2026-08-31 review verdict: two fresh-context axes on
  23a8710..37f2638 — one hard-leaning finding (README claimed cron.model pinned "per job";
  in-repo mechanism is the global setup.sh pin that jobs inherit — reworded, matching
  proposal §"cron.model" intent), plus query-map paraphrase drift and SSD phrasing
  (fixed); build-time window kept conservative for strangers (our 696 s measurement lives
  in the gate sign-off, not the README). Mid-gate artefacts to settle at sign-off: Notes
  entry preceded its rename follow-through commit (sequencing), and the gate's ticked
  clone-SHA needs re-pinning to the final shipped commit. Delta re-review (37f2638..59b782c):
  query-map/SSD/Notes FIXED; cron.model residual survived in the architecture diagram
  (pre-diff line 40) — fixed in the follow-up commit; "Knowledge sync (Phase 2)" phase
  phrasing is a deliberate scope description per spec-review finding (a2), kept.
- 2026-09-01 Phase-1 gate run (PAUSED at step 3) — Pi deployment complete: setup.sh
  real-mode exit 0, init_db seeded (+ idempotent re-run evidence), .env real incl. Drive
  trio verified by live OAuth exchange, drive_root=Fink-Labs, model/cron.model pinned to
  z-ai/glm-5.3-flash in-container, build 696s. DM test FAILED: bot exposed only built-in
  toolsets, no coordinator tools/persona. ROOT CAUSE: upstream plugin contract
  (hermes_cli/plugins.py:19) requires plugin.yaml manifest + __init__.py register(ctx);
  src/coordinator ships register() but NO manifest → discovery silently skips it.
  Fix (next session, TDD + full review): add src/coordinator/plugin.yaml (+ manifest
  schema test), verify discovery in-container, restart, re-run gate steps 2-9.
- 2026-08-29 Phase-gate red team (before T1.6, per AGENTS.md review protocol §4) —
  charter: break the core with NEW failing tests. Result: 12 real bugs / 54 attack cases;
  scheduling DST math held completely (explicit could-not-fail: Berlin ±1s flips, Lord
  Howe half-hour DST, Kathmandu +05:45, Kiritimati +14 wraparound, year-2500, spring-gap
  wakes immune by construction). Hard findings fixed in fix(phase1) commit 7808a35:
  duplicate/oversized telegram_id now ok:False (was raw IntegrityError/OverflowError),
  malformed/non-object/permissive-empty schema files now ConfigError (was
  JSONDecodeError/AttributeError/KeyError), validate_wake ASCII-only digits + non-str
  guard, setting_set digit/chat-id ASCII-only, db.DatabaseError now actually raised
  (connect/migrate wrap sqlite3.Error; class rebased Exception->sqlite3.Error to keep
  script contracts). 37 held-behavior keeper tests adopted (test_redteam_keepers.py);
  31 regression tests added; suite 156 -> 224. Delta re-review: FIXED, no regressions.
  Judgement calls recorded: (1) N1 low residual — permissive {} schema + non-mapping
  section (project: 5) still raises raw TypeError; unreachable with the shipped schema;
  (2) duplicate YAML keys silently last-win (PyYAML default) — strict loader is a
  possible future hardening; (3) wake-edit on an INACTIVE member emits an edit relay for
  the paused job (row stays inactive) — documented relay-precedence behavior; (4)
  DatabaseError rebasing to sqlite3.Error subclass keeps init_db/generate_agents_md
  except-clauses working.
- 2026-09-02 Phase-1 gate COMPLETED (steps 2-9 + Sign-off; T1.6 ticked) — resolution of
  the 2026-09-01 pause. Fix rounds (each TDD + two-axis fresh review + delta re-review;
  0 hard violations after arbitration): (1) cacd1b9 — plugin.yaml (v1 census fields,
  provides_tools == TOOL_SPECS) + package-level register(ctx) single-positional
  entrypoint wiring wire_runtime/SystemClock/default_db_path + intra-package imports made
  relative (upstream loads the dir as hermes_plugins.coordinator; absolute self-imports
  fail in-container); (2) 6b1e6ff — host dispatch contract: upstream invokes
  entry.handler(args, task_id/session_id/user_task) and _normalize_handler_result accepts
  str results only, so _bind takes **_host (ignored) and json.dumps at the boundary
  (handlers keep the dict contract); (3) de01106 — sqlite3 check_same_thread=False
  (gateway calls handlers from worker threads; serialized mode threadsafety==3 verified
  live on host+container; busy_timeout covers cross-process). The spec axis also caught a
  wrong runtime DB path (extra data/ segment vs the ./data:/opt/data/workspace mount)
  fixed in c76b8de BEFORE first deploy. Arbitrated judgement calls: SystemClock keeps the
  single datetime.now in src/ (rule-5 literal vs rule-3 adapter sanction — the injection
  root; recorded, user surfaced at sign-off); json.dumps default=str stringifies leaked
  types silently (explicit-serialization refactor candidate); Callable[..., str]
  loosens the HermesContext handler type (host's real signature is dynamic); real
  telegram_id committed in members.seed.yaml (identifier, not credential — owner
  directed). Owner-directed founder roster 626bf81 (telegram_id null tolerated by
  init_db). Gate runtime findings: plugins.enabled + top-level toolsets (kanban) are
  RUNTIME config, applied manually — setup.sh follow-up queued (a stranger clone would
  hit both gaps); kanban tools ship inside the hermes-telegram composite gated by the
  _check_kanban_mode check_fn (top-level toolsets flag); bot-authored workaround skill
  removed after the fixes (it encoded direct-DB bypasses); self-model incident — an
  owner-approved switch to google/gemini-3.6-flash broke the DM provider mid-gate,
  operator reverted + corrected the bot's memory + persona amended (d6e610e:
  verify-before-claiming + sanctioned valve flow; retest refused cleanly); doc
  corrections: `hermes cron trigger` -> `hermes cron run`, per-job model column ->
  fleet-inherit + fail-closed drift guard (cron/jobs.py:1771), cronjob relay evidence
  lives in /opt/data/logs/agent.log (tool_executor), not compose stdout; setup.sh bare
  `python` needs the repo venv on PATH (T0.13 known). Evidence: fire test delivered both
  fake-member greetings within ~1 min; one real unattended fire (checkin-1 @ 04:30 UTC =
  Juan's 06:30 Berlin wake) delivered; fake rows cleaned; roster pristine;
  AGENTS.md regen cycle proven across new sessions. Phase-1 exit criteria met.
- 2026-09-02 upstream evaluation (user-directed) — Hermes v0.21.0 (v2026.8.31,
  "Pantheon") released Aug 31; our pin v2026.8.27 (= v0.20.6) predates it by 911 commits
  (the release rolls up and documents the v0.20.1–v0.20.6 windows we already have, so the
  real delta is Aug 27→31). Full audit against the release notes + docs + plugin/cron/
  registry sources at the new tag: NO breaking changes on the surfaces we run — gateway
  compose shape (host networking, HERMES_UID/GID, ~/.hermes:/opt/data), cronjob tool with
  name-based edit, cron.model pin + fail-closed drift guard, cron-runs-cannot-manage-cron
  default, workdir= detachment, Telegram allowlists (all three vars), admin-bot privacy
  workaround, SOUL.md/AGENTS.md wiring, manifest census (our fields stay known; census
  gained v2 fields), register_tool signature. Notable findings: (1) user plugins are
  OPT-IN via plugins.enabled with upgrade grandfathering — the live Pi is covered, fresh
  clones are not (→ T1.7); (2) schema["description"] is the model-facing description,
  register_tool(description=...) is not copied into schemas lacking it (→ T1.8);
  (3) cron gained `cron doctor`, last_fire_error surfacing, continuity=true, per-job
  reasoning pins, and `cron.allow_agent_scheduling` (opt-in scheduled cron management —
  a future design decision for the DST recompute per proposal §6.4; deliberately NOT
  enabled now, reconciler stays prompt-not-process). Queued T1.7–T1.9 (Phase 1
  follow-ups) by owner direction ("full sequence": prep tasks, then pin bump).
- 2026-09-02 T1.7 — review verdict: no hard violations (two fresh axes, fixed point
  c26c8b2); fix round 9a98dc9: shell-quoted printed config values (bash-glob safety for
  copy-paste) + extracted the shared print_in_container_fallback block (duplication
  finding); delta re-reviewed FIXED, escaping traced byte-exact. Judgement calls: the
  config-set values re-assert the template baseline on re-run (clobber-by-design for the
  two template-owned keys, documented in the task Notes); the fallback's restart line
  goes beyond the two named commands (operational necessity when applied post-first-boot).
- 2026-09-02 T1.8 — review verdict: no hard violations; BOTH axes independently flagged
  the spread order (a future schema-level "description" could shadow the declared single
  source) — fixed in 9a657f9 ({**spec["schema"], "description": ...}), delta re-reviewed
  FIXED. register_tool(description=...) kept alongside the schema copy — both derive from
  the one ToolSpec field, synchronized by construction.
- 2026-09-02 T1.9 — pin resolved v2026.8.31 → tag object 6e8f8418 → commit 29112bef
  (GitHub API; tagger 2026-08-31T19:29:39Z); the drift-guard test validated the rendered
  `docker compose config` locally. Review finding "nothing enforces compose-URL ==
  HERMES_REF" DISMISSED with evidence: tests/test_compose.py asserts exactly that and
  ran green on the new SHA. Manual Pi steps (rebuild + abbreviated gate) remain for the
  operator per docker/README.md.
- 2026-09-02 T1.9 Pi execution (owner-directed, driven over SSH from the Mac) — pushed
  09b52f2..864f585, Pi pulled to 864f585, `docker compose build` exit 0 in 488 s (warm
  cache; gate build was 696 s), `up -d` recreated the container. Headless verification:
  `hermes --version` = v0.21.0 (2026.8.31) in-container; `hermes plugins list` shows
  coordinator enabled (user, 0.1.0); 2 established TCP connections to Telegram DC ranges
  (long-poll live); `plugins.enabled`/`toolsets`/`cron.model` (z-ai/glm-5.3-flash)
  intact; checkin-1 active — last run ok 2026-09-02T04:30Z, next 2026-09-03T04:30Z.
  Boot-log warnings benign (check_fn gates for unrelated tools; one auxiliary-client
  PAID-lane notice re the `google/gemini-3.6-flash` OpenRouter fallback — cost surface
  flagged to owner, `auxiliary.free_only`/`auxiliary.openrouter_model` are the knobs).
  Remaining (human-only): the DM half of the abbreviated gate — persona voice, 7 tools,
  roster, kanban. Note: only checkin-1 exists — correct state: the other founders'
  telegram_ids are still null; their jobs are created conversationally at onboarding.
- 2026-09-02 auxiliary fallback pin (owner-directed) — boot log showed the auxiliary
  client's PAID-lane warning: the step-2 OpenRouter fallback engaged with the built-in
  default `google/gemini-3.6-flash` (agent/auxiliary_client.py `_OPENROUTER_MODEL` at
  v0.21.0; text-lane only — vision tasks resolve through a separate vision-capable
  path). Owner pinned `auxiliary.openrouter_model = deepseek/deepseek-v4-flash-0731`
  (OpenRouter catalog: $0.065/M input, $0.18/M output, 1.31M context, text-only; not a
  `:free` SKU). `auxiliary.free_only` stays false — when true, Hermes SKIPS the
  OpenRouter fallback entirely for a non-free model (`_try_openrouter`), which would
  contradict the owner's choice. The PAID-lane warning remains one-time-per-model per
  process by design; the spend is operator-sanctioned (money-valve philosophy: deliberate
  + documented). Applied in-container via `hermes config set`, gateway restarted, value
  verified post-restart; main chat model and cron.model untouched.
- 2026-09-02 T2.1 — review verdict: no hard violations (two fresh blind axes, fixed point
  0eed164; spec axis probed exit codes, log contents, resync gate, rc propagation live in a
  mktemp sandbox). Both axes flagged the unchecked checkbox — DOWNGRADED to process note:
  the orchestrator protocol marks checkboxes in the post-review chore commit (T0.2 note 6
  precedent). Judgement calls: (1) RCLONE_REMOTE required, RCLONE_ROOT_FOLDER_ID optional
  per .env.example's "optional:" comment — with the folder id unset the wrapper bisyncs the
  remote ROOT into data/project/ silently; spec-consistent, but a loud stderr warning is the
  cheap fix candidate if the Pi gate surprises; (2) folder id maps to rclone's documented
  --drive-root-folder-id (belt-and-braces with the rclone.conf pin setup.sh already writes);
  (3) --boot is a tagging flag ([boot] log/console marker, no behavior change) — boot-time
  invocation wiring is a later task; the phase-2 gate observes cadence+boot on the Pi;
  (4) fail-loud pre-flight guards beyond the spec text, none silent (rclone-on-PATH pointer;
  non-resync real run refuses when data/project/ is absent → one-time --resync
  --i-know-what-im-doing bootstrap; resync mkdir -p's the mirror; usage()+exit-2 convention
  on every validation error; rclone rc propagation with a log end-line); (5) --dry-run is
  strictly zero-side-effect (prints the composed command, no log write, no mkdir) — what
  makes the suite offline; (6) -v added to the composed command for the file-level detail
  the phase-2 gate needs; (7) `date -u` log stamps are I/O labeling, not schedule math
  (rule-4 carve-out documented in the header); (8) tests force the no-rclone path via a
  symlinked fake-bin PATH (corollary: subprocess env= resolves the CHILD executable on the
  child PATH, so tests resolve bash via shutil.which from the parent env); (9) batch red
  (all 9 tests before implementation) per the task brief's explicit red→green instruction.
  Standards-axis smells (all cosmetic, no fix round): flag/env docs duplicated in header vs
  usage() heredoc (one canonical list candidate); repeated date stamp (stamp() helper
  candidate); RUN_KIND/LOG_PREFIX mode clump (only if the script grows). YAGNI: bisync
  lockfile/staleness recovery left to T4.2's runbook + phase-4 backups; real (non-dry) mode
  untested by design (needs rclone+credentials; dry-run covers the exact arg array).
- 2026-09-02 T2.3 — DOC task executed directly per orchestrator protocol (no subagent, no
  review; make check green). Judgement calls: (1) setup.sh gains step 7 (labels renumbered
  1/6..6/6 → 1/7..7/7, T1.7 pattern) — seeds project-template/ into
  ${PROJECT_DATA_ROOT:-$REPO_ROOT/data}/project with per-file exists-guards; existing files
  are never overwritten (the T2.3 guarantee); (2) three testability env knobs added
  (CONFIG_YAML / CONFIG_SCHEMA / PROJECT_DATA_ROOT; precedent RCLONE_CONFIG_PATH from T0.13)
  so the real-mode seed is testable without writing the repo tree — defaults keep production
  behavior identical; (3) the five empty dirs carry .gitkeep markers (git does not track
  empty dirs); they land on the Drive as invisible dotfiles, acceptable; (4) the spec's seed
  list is CLOSED (README.md, docs/index.md stub, inbox/ assets/ people/ journal/ .archive/)
  — data/project/templates/ and docs/product/ are deliberately NOT seeded: the human fills
  brief.md per proposal step 8; seeding the repo's templates/ dir into data/project/
  templates/ is a cheap follow-up candidate left to the owner; (5) new real-mode smoke test
  runs the full script with fixture env (no mocks, offline): proves exit 0, seeded contents,
  and never-overwrite (a pre-existing custom README.md survives; docs/index.md edited
  between runs survives run 2), using the committed example yaml as the valid config.
- 2026-09-03 v6 restructure (owner directives D1–D4, docs/audit/2026-09-03-audit.md) —
  T2.5 DOC executed directly (no subagent, no review; make check green): proposal.md
  amended to v6 (§1/§2/§3/§5/§6/§7/§8/§10) and Phase 2 re-queued as T2.5–T2.17. The old
  T2.4 (bisync gate) is RETIRED: the gate was abandoned mid-run at the Drive-OAuth
  incident (running record: .scratch/phase2-gate-running-record.md) and bisync never
  reached production — which is what makes D2's deletion safe; T2.1's wrapper is removed
  with its tests by T2.11. AGENTS.md: two stale lines amended (mission "rclone-bisynced"
  sentence; scripts/ repo-map entry; schema comment v5→v6) — mechanical consequences of
  the sanctioned redesign, flagged to the owner; the constitution's rules are untouched.
  D1 10-minute check recorded (proposal §7.6, §8.2): `hermes config set` CAN write
  env-style keys (docs: auto-routes env vars to HERMES_HOME/.env), but our compose
  injects TELEGRAM_ALLOWED_USERS from the repo-root .env as a container env var and
  container env always shadows dotenv values — the config-set path would be silently
  ignored, so allow.sh edits the repo-root .env and applies with `docker compose up -d`
  (restart semantics unchanged either way). Leaked founder ID ([redacted-founder-id]) flagged to
  the owner per D3 — history purge is the owner's call, out of scope.
- 2026-09-04 T2.7 — review verdict: spec axis 2 HARD (key-absent branch corrupted a
  .env lacking a trailing newline — separator written before the content; the
  spec-named EMPTY allowlist case was untested and aborted under bash 3.2 set -u);
  standards axis 0 HARD + 4 judgement (empty-value crash, head -n 0 on BSD, the
  .env.allow* temp file escaping .gitignore, duplicate-arg dupes). ONE fresh fix
  round 4ea371f fixed all six (append order, :- guard, line-1 guard, .env.allow*
  ignore, requested-ID dedup, never-restart rationale in output — the old dry-run
  assertion over-pinned and was weakened to forbid only a suggested restart command);
  delta re-review: FIXED, no regressions (bash 3.2 exercised for real — the suite
  shell IS 3.2); make check green (269). Judgement calls: (1) existing non-numeric
  tokens in the allowlist value are preserved as-is (only REQUESTED args are
  validated) — owner data is never silently dropped; (2) the no-op run skips the
  compose apply (idempotence means zero side effects, not just byte-equality);
  (3) ALLOW_ENV_FILE knob mirrors the T0.13/T2.3 testability-knob precedent. Ops
  note: the suite exercises bash 3.2 (macOS) while the Pi runs bash 5 — both now
  covered by the guards.
- 2026-09-04 T2.8 — review verdict: spec axis 1 HARD (the prescribed description test
  was missing — reverting the new member_add description kept the suite green);
  standards axis 0 HARD. ONE fresh fix round 2f94d6a: description-content pin added
  (red-verified against the pre-T2.8 text — six assertions, all with teeth), row-id
  assertions strengthened on both duplicate tests, persona "and removals" scope creep
  reverted (T2.9 will add it when the tool exists); delta re-review: FIXED, no
  regressions; make check green (275). Judgement calls recorded per rule 8: (1) a
  duplicate against an INACTIVE row still adds (D1 letter — the NEW row is the only
  active one; reactivation via member_update is the other path); (2) whitespace-only
  names remain storable — _require_str rejects only "" and the duplicate matcher trims
  on compare but never on store (pre-existing gap, newly exercised; tighten only if a
  real roster ever trips on it); (3) the duplicate-active-name invariant is add-path
  only — member_update renames can still create what member_add rejects (recorded as
  a future-need trigger, not built: the add error summary points at member_update, so
  the gap is user-reachable); (4) casefold matching makes ß↔ss collide (stricter,
  defensible).
- 2026-09-04 T2.9 — review verdict: standards axis 1 HARD (MembersRepo.delete was the
  first multi-statement sequence on the shared worker-thread connection and added none
  of the locking db.py's docstring pre-agreed, and had no rollback — a failed members
  DELETE left the checkins DELETE pending for a later unrelated commit to persist);
  spec axis 0 HARD + 3 judgement. ONE fresh fix round 41ad009: db.py gains module-level
  WRITE_TRANSACTION_LOCK (RLock) that delete holds across both DELETEs + commit
  (connect() docstring now points at it), rollback-on-sqlite3.Error re-raised so the
  atomicity claim is true, and a deterministic ABORT-trigger test proves both member
  row and checkins survive a failed delete with nothing pending (red→green);
  wake⇒job docstring reworded as the documented assumption it is; stale "7 tool names"
  docstring fixed; delta re-review: FIXED, no regressions; make check green (281).
  Judgement calls: (1) owner-only remains persona-layer enforcement (the plugin has no
  caller identity; technical enforcement = documented escalation, not scope); (2) the
  wake⇒job heuristic can target a nonexistent job when a create relay never ran — same
  assumption the pause/edit relays make, unverifiable from plugin state; (3) Notes-level
  residues left: one stale "All 7 schemas" test docstring and the wiring tuple pinning
  7/8 handler identities (member_delete covered via set equality) — cosmetic coverage
  nit, tighten opportunistically.
- 2026-09-04 T2.10 — review verdict: standards axis 0 HARD + 4 judgement, spec axis
  0 HARD + 3 judgement; the cross-axis flagship — the dropped digest_time KEY survived
  as a stored settings ROW on upgraded stores, so setting_get answered it there while
  fresh stores rejected it (schema converged, behavior did not) — was arbitrated fix-
 worthy: fix round c814dc5 purges the row inside migration 002's guarded transaction
  (red-verified end-to-end), corrects the convergence-test comment (DROP COLUMN does
  rewrite stored DDL; the real reason for table_info comparison is fixture-DDL
  formatting), strengthens the test to assert status_days absent from the upgraded
  stored members DDL, and fixes the "three keys" docstring; delta re-review: FIXED, no
  regressions; make check green (286). Judgement calls: (1) proposal.md:248 comment
  de-dial edit was outside the task block's named files — covered by the v6-wide
  proposal-sanction recorded at T2.5/T2.16, disclosed here; (2) convergence is asserted
  on engine-visible columns + table sets, not sqlite_master.sql text (fixture DDL
  formatting differs from schema.sql; text comparison brittle) — deviation from the
  block's literal "sqlite_master identical", documented in the test; (3) Notes-level
  residue: test_handlers.py:1033 docstring still says "the three DEFAULTS keys"
  (cosmetic, outside the fix mandate); (4) DROP COLUMN requires SQLite >= 3.35 — no
  floor asserted; older engines fail loudly and roll back (Pi/venv ship >= 3.37).
- 2026-09-04 T2.11 — review verdict: standards axis 1 HARD (project-template/README.md
  intro still promised the deleted bisync cadence — "two-way sync, roughly every 15-30
  minutes and at container boot" — internally inconsistent with its own rewritten
  conflict note, and invisible to T2.16's bisync/rclone keyword sweep); spec axis 0
  HARD + 3 judgement. ONE fresh fix round 40779af rewrote the intro to the v6 Drive
  story; delta re-review: FIXED, no regressions (whole-file keyword sweep clean); make
  check green (271 — the 15 deleted sync tests account for the drop). Judgement calls:
  (1) docker/README.md:57 "rclone-bisynced project mirror" routed explicitly into
  T2.12's block (parity with the compose comment it already updates); (2) README
  quickstart step-4 rclone.conf sentence routed into T2.12's README edits; (3) the
  generate_agents_md.py bisync sentence stays T2.15-scoped (its golden tests are
  rewritten there anyway); (4) docs/verify/phase2.md is the retired old gate — T2.17
  rewrites it; (5) between this commit and the T2.17 gate the digest upload
  instruction is doc-forward by design ($GAPI verified at the gate's FIRST item).
- 2026-09-04 T2.12 — review verdict: spec PASS (red-first proven: swapping back the
  pre-T2.12 compose makes the drift guard fail listing exactly the six legacy keys);
  standards 0 HARD + 3 judgement. Judgement calls: (1) LEGACY_DRIVE_VARS scrub set
  kept in test_compose.py — speculative-generality flagged but retained as rule-7
  defense-in-depth at zero cost (prevents a dev's exported legacy Drive secrets from
  reaching render/test output); (2) docker/README.md mount cell omits the compose
  comment's "$GAPI upload, not mirrored" clause — phrasing parity only, substance
  fixed; (3) doc-forward pointers (.env.example "phase-2 gate's FIRST item") stay
  temporarily stale until T2.17 writes the gate — same doc-forward-by-design class as
  T2.11 note (5); (4) Notes-level residue: test_setup_smoke.py:2 module docstring
  still says "step 7" after the 7->6 renumber (one-word cosmetic).
- 2026-09-04 T2.13 — review verdict: spec axis 2 HARD (the specced out-of-band-DELETE
  integrity test was missing — replaced by an insert-direction variant justified by a
  wrong version claim; the knowledge convergence was unpinned), standards axis 1 HARD
  (heading-suffix collision: a literal "## Notes (2)" in source markdown collided with
  the generated suffix -> UNIQUE IntegrityError -> file unindexable) + 1 J later
  resolved (fence-awareness, 19775e8). TWO fix rounds: 19775e8 (delete-direction test
  added on the suite's SQLite 3.50.4, convergence pin extended to knowledge columns —
  verified red both ways on DDL drift, fence-aware chunker, proposal version claim
  corrected) and 4d2f4f8 (collision-proof document-order used-set suffix resolution,
  red-first + exhaustive 1,364-document sweep collision-free and deterministic across
  PYTHONHASHSEED). Delta re-reviews: FIXED, no regressions; make check green (293).
  Arbitration + judgement calls: (1) the proposal.md integrity-check edit was
  unauthorized by the block but factually correct on both SQLite builds — BLESSED and
  disclosed here rather than reverted (reverting a verified correction serves no one);
  (2) non-text-document branch is T2.14-owned (its block names the absent-content
  row); (3) residual CommonMark fence gaps (nested fences, closing fence with trailing
  text, indented fences) are Notes-level — documented toggle semantics, not a breach;
  (4) watermark is lexicographic MAX — valid only for Drive's Z-normalized RFC3339
  modifiedTime, documented assumption; (5) UNIQUE(file_id, heading) holds for NULL
  headings via the chunker's at-most-one-preamble invariant, not the constraint;
  (6) malformed MATCH raises OperationalError from the repo primitive — routed into
  T2.15's block (ok:False at the tool layer); (7) hygiene: .scratch/ review-probe
  debris gitignored so a bare make check passes in the working tree.
- 2026-09-04 T2.14 — review verdict: spec PASS (every block clause probed hands-on:
  two-call protocol, non-text rows, validate-then-apply, empty-ingest no-op, nested
  model-facing schema, 5-tuple wiring); standards axis 1 HARD (the unwired-repo
  KeyError guard shipped with no covering test) + 3 judgement. ONE fix round d8b2d45:
  guard test added (red→green), ToolSpec.takes_knowledge declared NotRequired[bool]
  (docstring now true; explicit False entries kept; the monkeypatch ToolSpec literal
  omitting the key became type-correct), knowledge_sync docstring documents the
  lenient contract (files:null = plan call per the handlers-wide null-is-absent
  convention; content:null = non-text; same-batch duplicate file_ids last-wins with
  synced counting entries), mirror-test docstring softened to field-level claims;
  delta re-review: FIXED, no regressions; make check green (304). Judgement calls:
  (1) stale re-ingest can lower the MAX watermark (older modified_time wins) —
  pre-documented accepted design (T2.13 note 4); (2) the lenient-direction drift
  (handler accepts null where the schema forbids it) is the repo-wide convention,
  documented rather than tightened; (3) the handler-signature polymorphism
  (takes_knowledge + two casts in dispatch) was reviewed as the narrowest mypy-strict
  solution — the 8 non-knowledge handlers keep the uniform 5-dep signature.
- 2026-09-04 founder-ID history purge (owner-directed follow-up to the T2.6 flag) —
  git filter-branch rewrote ALL refs: every blob and commit message containing the
  leaked founder Telegram ID now carries [redacted-founder-id] (members.seed.yaml in
  historic commits: telegram_id: null). Old refs/reflogs/objects expired and pruned;
  verified: git grep + pickaxe over main = zero occurrences; the only remaining ref
  is main (453b2e4). CONSEQUENCES: (1) every commit SHA cited in this Notes log
  before the purge describes pre-rewrite history and no longer resolves — the
  rewrite is SHA-destructive by nature; (2) the origin remote still hosts the old
  history — the owner must force-push (git push --force origin main) and re-clone
  the Pi (or fetch + reset --hard origin/main after the force-push); (3) GitHub-side
  cached views/PRs may retain the old commits until contacted — owner decision;
  (4) docs/verify/phase1.md gate evidence now reads [redacted-founder-id] — the
  sign-off record remains readable minus the leak.
- 2026-09-04 T2.17 — MANUAL-GATE doc written (box stays UNCHECKED for the human).
  Phase-gate red team (AGENTS.md #4) attacked the knowledge subsystem: 13 attack
  tests, 6 real bugs — B1 lone-surrogate bind failure skipped the rollback and
  poisoned the shared conn (next sync silently committed the deletes: cache loss),
  B2 NUL-truncated MATCH returned wrong hits, B3 fence toggle ignored type/run
  length, B4 splitlines over-split (U+2028/2029/NEL/VT/FF), B5 empty-content files
  vanished from the cache and starved the watermark, B6 lexicographic watermark
  mis-ranked offset forms. Adoption fix ad2d00a: rollback-on-BaseException in
  replace_file, C0-char query guard at the tool layer, CommonMark fence rule,
  markdown-only line splitting, title/path-only rows for absent/empty/whitespace
  content, UTC-canonical modified_time at ingest; all 13 attacks adopted as
  regression tests (tests/unit/test_knowledge_redteam.py, red-first). Delta
  re-review: FIXED, no regressions; make check green (325). Judgement calls:
  (1) encode-failures still propagate out of knowledge_sync (payload encoding is
  not payload validation) but per-file commits + rollback keep the store consistent
  — UX polish is a Notes candidate; (2) the NUL guard lives at the tool layer — the
  repo primitive still truncates at NUL by SQLite design (only the handler calls it);
  (3) docs/verify/phase2.md is the T2.17 deliverable — the human runs it on the Pi.
- 2026-09-04 T2.15 — review verdict: spec axis 1 HARD — a PROCESS finding: the
  generator rewrite existed only as an uncommitted working-tree change (the T2.15
  commit's own goldens contradicted its committed generator; make check green held
  only with the uncommitted file) — my git add missed scripts/. FIXED BY AMEND:
  773a3dc -> 56a8156 (unpushed; the amended committed tree passes make check with a
  clean worktree, verified). standards axis 0 HARD + 6 judgement. Also added:
  people/ parity in the generator's Drive tree (proposal §3). Judgement calls:
  (1) README's old query-map line (search_files) and config.schema.json's drive_root
  "(rclone bisync root)" routed explicitly into T2.16's acceptance sweep; (2) new
  public callables (knowledge_search, KnowledgeRepo.search, knowledge_sync,
  member_delete) carry multi-line docstrings — rule 6 letter vs practice, consistent
  with pre-existing db.py precedent; (3) malformed-MATCH routing into T2.15's block
  done (ok:False at the tool layer, verified); (4) KnowledgeRepository.search
  Protocol docstring does not mention the KnowledgeSearchError raise the concrete
  class documents — coverage nit, tighten opportunistically; (5) proposal.md:373 and
  ROADMAP T5.1 mention search_files as deferred/source-of-truth docs — excluded from
  the sweep by design.

- 2026-09-04 T2.6 — review verdict: standards axis 2 HARD (the documented local-seed
  path was not actually gitignored — guidance claimed "gitignored" with no rule;
  multi-line test docstring vs rule 6); spec axis PASS with the gitignore gap as a
  judgement note. ONE fresh fix round d2e560f: config/members.local.yaml added to
  .gitignore (check-ignore verified) + docstring collapsed; delta re-review: FIXED, no
  regressions; make check green (252). Judgement calls: (1) commit 8f99f3a's message
  re-quotes the leaked founder ID in plaintext — a second copy that would survive a
  file-only purge; left for the owner's history-purge decision; (2) the seed header and
  README reference scripts/allow.sh, which lands with T2.7 (forward reference resolves
  next task); (3) the docs name config/members.local.yaml as the local-seed example —
  the .gitignore rule pins exactly that name.
