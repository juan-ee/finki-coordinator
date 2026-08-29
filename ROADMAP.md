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

- [ ] **T0.10 `scripts/init_db.py`** — CLI `--db PATH [--seed config/members.seed.yaml]`:
  mkdir -p, connect+migrate, seed members once (marker `seeded_at` in settings), exit 0
  idempotently. Tests: `tests/unit/test_init_db.py` — fresh seed inserts N members;
  second run = no duplicates; run without seed = empty members table.

- [ ] **T0.11 `DOC` — config artifacts finalized** — `config/members.seed.yaml`
  (4 members: 3× `America/Guayaquil`, 1× `Europe/Berlin`, wakes 08:00/08:30/09:00/08:30),
  `config/config.example.yaml`, `.env.example` (exact content per `proposal.md` §2 incl.
  `TELEGRAM_ALLOWED_USERS` + group-scoped vars + OpenRouter-limit comment).
  Tests: `tests/unit/test_seed_file.py` — seed parses; every tz resolvable via zoneinfo;
  wakes match `^([01]\d|2[0-3]):[0-5]\d$`.

- [ ] **T0.12 `scripts/generate_agents_md.py`** — renders the **runtime**
  `data/project/AGENTS.md`: roster table (from members DB), folder structure block,
  query map (mission → `docs/product/brief.md` first; decisions → docs/decisions/ …),
  editorial policy summary. Deterministic output; overwrite in place.
  Tests: `tests/unit/test_generate_agents_md.py` — golden-file render from fixture DB
  (contains all member names/timezones + all query-map bullets); rerun is byte-identical.

- [ ] **T0.13 `scripts/setup.sh`** — `set -euo pipefail`; steps: check required `.env`
  keys; validate `config/config.yaml` via `python -m coordinator.config validate`;
  write rclone.conf from env (client id/secret/refresh, remote, root folder id);
  install `prompts/persona.md` → `${HERMES_HOME:-$HOME/.hermes}/SOUL.md`; apply Hermes
  config: model + `cron.model` pin + `timezone: UTC` (via `hermes config set`, with a
  printed manual fallback if CLI absent); print next steps. `--dry-run` prints actions
  without writing. Tests: `tests/test_setup_smoke.py` — `bash -n` passes; `--dry-run`
  with fixture env exits 0 and writes nothing (snapshot tmp dir).

- [ ] **T0.14 `docker-compose.yml` + `docker/`** — template compose: build hermes-agent
  from upstream repo at ref pinned in `docker/HERMES_REF` (build arg), mount
  `./src/coordinator` into the container's plugin directory, mount `./data` for our DB +
  project mirror, `TZ=UTC`, `HERMES_UID/GID` passthrough, gateway command per upstream
  compose. Document the Pi build (slow, one-time) in `docker/README.md`.
  Tests: `tests/test_compose.py` — `docker compose config` exits 0 (skip if docker
  absent); HERMES_REF file contents appear in the rendered config.
  **Phase-0 gate:** all boxes above checked.

## Phase 1 — Core bot

- [ ] **T1.1 `DOC` — `prompts/persona.md`** (→ SOUL.md) — group-PM voice rewritten from
  OpenExecutive `executive_persona.py` (operator tone, 2–3 key variables, end with
  "so, what do we do next"). Must encode: relay `cron_relay` **verbatim** (never recompute
  schedules), read `docs/product/brief.md` before major asks, editorial policy
  (drafts → `inbox/`, never write `docs/` directly), members manage only their own row,
  roster admin owner-only. Acceptance: self-review checklist at file bottom all ✅.

- [ ] **T1.2 `DOC` — `prompts/skills/check-in/SKILL.md`** — check-in flow: greet →
  ask done/next/blockers → `checkin_submit` → confirm + surface relevant teammates'
  blockers. One worked example dialogue embedded.

- [ ] **T1.3 `DOC` — `prompts/skills/digest/SKILL.md`** — 17:00 job: `checkins_by_date`
  → `kanban_list` → inbox count → write `journal/YYYY-MM-DD.md` (format template embedded:
  Check-ins table with ✅/⚠️, Board moves, Inbox, Flags) → post condensed summary to the
  delivery chat → run `scripts/sync.sh` (on-demand beat, proposal §3).

- [ ] **T1.4 `DOC` — `prompts/skills/schedules.md`** — "recalculate all check-in
  schedules" procedure (member_list → build edit relays for every active member → relay);
  twice-a-year DST note; why it must be asked interactively (cron sessions cannot manage
  cron jobs).

- [ ] **T1.5 Integration test — `tests/integration/test_day_flow.py`** — a full day on a
  tmp DB: seed 4 members → 4 check-ins (one corrected, assert upsert) → `checkins_by_date`
  returns 4 → set digest settings → assert `cron_relay` schedule strings for Guayaquil and
  Berlin members at a winter instant and a summer instant.

- [ ] **T1.6 `DOC` `MANUAL-GATE` — `docs/verify/phase1.md`** — Pi verification script:
  ARM64 build succeeds (record duration); plugin tools visible in a DM session;
  kanban board reachable; timezone matrix (two fake members, `cronjob trigger`, verify
  delivery in expected window); `cron.model` pin visible on jobs; AGENTS.md injected
  (ask the bot for its query map). Leave unchecked for the human.

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
