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
| 2 Knowledge | Sync wrapper + doc templates + project seed ship | T2.4 manual on Pi |
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

## Phase 2 — Knowledge

- [ ] **T2.1 `scripts/sync.sh`** — bisync wrapper: logs to `data/logs/sync.log`, boot mode
  flag, refuses `--resync` without `--i-know-what-im-doing`, uses `RCLONE_REMOTE` +
  `RCLONE_ROOT_FOLDER_ID` from env. Tests: `tests/test_sync_smoke.py` — `bash -n`;
  dry-run without env vars exits non-zero with actionable message.

- [ ] **T2.2 `DOC` — `templates/`** — `brief.md` (mission/goals/success criteria/
  constraints), `adr.md` (context/decision/consequences), `meeting-notes.md`,
  `proposal.md`.

- [ ] **T2.3 `DOC` — `project-template/` + setup copy step** — seed content for
  `data/project/`: `README.md` (human onboarding), `docs/index.md` stub, empty
  `inbox/ assets/ people/ journal/ .archive/`; `setup.sh` copies it into `data/project/`
  on first boot (never overwrites existing files).

- [ ] **T2.4 `DOC` `MANUAL-GATE` — `docs/verify/phase2.md`** — bisync cadence observed;
  conflict drill (edit same file both sides, recover via runbook, no data loss);
  AGENTS.md regeneration; doc-extraction check (PDF uploaded on Drive readable by agent).

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
  optional rclone copy to Drive; `--dry-run`. Tests: `tests/test_backup_smoke.py` —
  `bash -n`; dry-run writes nothing.

- [ ] **T4.2 `DOC` — `docs/runbooks/`** — `bisync-recovery.md` (the never-blind-resync
  procedure), `restore-backup.md`, `new-member.md` (proposal §8.2, verbatim steps),
  `dst-resync.md` (the twice-a-year conversation).

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
