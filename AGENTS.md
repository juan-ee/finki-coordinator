# AGENTS.md — Engineering Constitution for This Repository

**Audience:** any coding agent (or human) implementing the Hermes coordinator template.
**Read order:** this file → `proposal.md` (what & why) → `ROADMAP.md` (the work queue).

> ⚠️ Two files named AGENTS.md exist in this project's world. **This one** governs agents
> that *build the template*. A different, generated `data/project/AGENTS.md` will later
> govern the *runtime coordinator bot*. Never confuse them; never write runtime content here.

---

## Mission

Build a clonable template repo that boots a Telegram-based async project coordinator on a
Raspberry Pi 5 (Docker, hermes-agent at a pinned ref), with: a Python coordinator plugin
(SQLite members/checkins/settings, 7 tools), UTC-anchored timezone-safe cron scheduling,
an rclone-bisynced Google Shared Drive knowledge base, and an OpenExecutive-derived persona.
Quality bar: it must survive being cloned and run by a stranger with only the README.

## Repository map (target layout)

```
AGENTS.md                  ← this file (engineering constitution — always loaded)
ROADMAP.md                 ← atomic work queue; the agent works top-to-bottom
proposal.md                ← architecture & decisions (source of truth for WHAT/WHY)
pyproject.toml             ← package + dev tooling (pytest, ruff, mypy, coverage)
uv.lock · .python-version  ← locked toolchain. uv is THE package manager (dev/CI only —
                             the Pi runtime uses the hermes container's own Python)
Makefile                   ← make install | test | lint | type | check
src/coordinator/           ← the Python package (pure core, testable)
  config.py                ← load + validate config.yaml against the JSON Schema
  scheduling.py            ← ALL timezone→cron math (pure, no I/O)
  db.py                    ← connection factory (WAL, busy_timeout) + migrations
  repositories.py          ← MembersRepo / CheckinsRepo / SettingsRepo
  handlers.py              ← tool payload validation + orchestration + relay building
  hermes_plugin.py         ← thin adapter: register tools with Hermes (no logic here)
  schema.sql               ← SQLite DDL (v5 schema from proposal §1)
scripts/                   ← init_db.py · generate_agents_md.py · setup.sh · sync.sh · backup.sh
config/                    ← config.example.yaml · config.schema.json · members.seed.yaml
prompts/                   ← persona.md (→ SOUL.md) · triage.md · skills/check-in · skills/digest
templates/                 ← brief.md · adr.md · meeting-notes.md · proposal.md (doc templates)
tests/                     ← unit + integration (no network, no real secrets, tmp paths only)
docs/verify/               ← per-phase manual gate scripts (Pi tests that can't be automated)
docker/                    ← HERMES_REF pin file + compose notes
```

## Hard rules (non-negotiable)

1. **TDD.** No production code without a failing test written first (red → green →
   refactor). Exceptions: tasks marked `DOC` in ROADMAP.md (pure markdown/config examples).
2. **Atomic work.** Exactly one ROADMAP task per change set. Update its checkbox, commit
   with `feat(T0.4): …` / `test:` / `chore:` style, run `make check` before committing.
3. **Pure core, impure shell.** Business rules live in pure, typed modules
   (`scheduling.py`, `handlers.py`, `config.py`). I/O (SQLite, files, Hermes `ctx`,
   subprocess) lives in thin adapters (`db.py`, `hermes_plugin.py`, scripts). Adapters
   contain **no** business logic; the core contains **no** imports of Hermes or the OS.
4. **Timezone law.** Every schedule computation lives in `scheduling.py` and nowhere else.
   Never compute a schedule in a script, handler, or prompt. Never let an LLM or agent do
   timezone arithmetic at runtime — it relays pre-computed strings (proposal §1).
5. **SOLID, in practice.** SRP: one reason to change per module. DIP:
   `handlers.py` depends on small `Protocol` interfaces for repos and clocks; concrete
   wiring happens only in `hermes_plugin.py` and tests. Time is injected
   (`Clock` protocol), never `datetime.now()` in `src/`.
6. **Typing & style.** `mypy --strict` clean on `src/coordinator/`; `ruff format` +
   `ruff check` (line length 100). One-line docstring on every public function.
7. **Secrets.** Never in code, tests, fixtures, or logs. Tests use tmp paths and fakes.
8. **YAGNI.** Implement exactly the task spec. If you spot a needed improvement, record it
   in the task's *Notes:* line instead of expanding scope. The proposal's risk list and
   dropped features (reconciler, onboarding interview, per-member profiles) stay dropped.
9. **Tests.** pytest, AAA pattern, deterministic: inject the clock, use `tmp_path`, no
   network, no Docker in unit tests. DST tests must assert on explicit UTC instants
   (e.g., 2026-03-29 and 2026-10-25 EU transitions), never on "local now".
10. **Runtime state.** Never touch `data/**`, never commit `.env`, never add a dependency
    that isn't in the task spec.

## Tool result contract (every handler)

```python
{"ok": bool, "summary": str,          # one line for the LLM to relay to the human
 "cron_relay": None | {               # present when a schedule changed — pre-computed
   "tool": "cronjob", "args": {...}}, # verbatim-executable by the agent (proposal §1)
 "data": {...}}                       # structured payload (rows, ids) — LLM renders prose
```

## Commands

Prerequisite: `uv` installed (https://docs.astral.sh/uv/ — `brew install uv` or the
official installer). `uv.lock` is committed and authoritative; never hand-edit it.

```
make install    # uv sync (creates .venv from uv.lock, dev deps included)
make test       # uv run pytest -q (unit + integration)
make lint       # uv run ruff format --check && uv run ruff check
make type       # uv run mypy --strict src/coordinator
make check      # lint + type + test  → MUST pass before every commit
```

Every Makefile target wraps `uv run`; agents never invoke `pip`, `pytest`, or `mypy`
directly — always through `make` (or `uv run <tool>` when a Makefile target doesn't exist).

## Roadmap execution protocol

1. Open `ROADMAP.md`, take the **first unchecked** task (respect phase order; tasks within
   a phase may be done in order of appearance unless marked parallel).
2. Write the failing tests named in the task. Run them (red).
3. Implement the smallest code that passes. Refactor toward the constitution. `make check`.
4. Mark `- [x]` in ROADMAP.md, commit `feat(T1.2): digest skill + tests`.
5. Tasks tagged `MANUAL-GATE` cannot be completed by a coding agent: produce/refresh the
   `docs/verify/phaseN.md` script and leave the box unchecked with a note for the human.
6. Blocked? Mark the task `⚠️ blocked: <reason>` in ROADMAP.md; continue with the next
   task that doesn't depend on it. Two rounds of blocking on the same dependency = stop
   and report.

## Review protocol (adversarial — every non-DOC task)

Same-agent self-review is worthless: the implementer's context rationalizes its own code.
Review is therefore always **fresh-context and spec-only**:

1. **Pin the base.** Before dispatching a task, record the pre-task SHA
   (`git rev-parse HEAD`) — this is the review's fixed point.
2. **Review.** Run the `code-review` skill (or reproduce its process exactly) with:
   - fixed point = the pre-task SHA,
   - **Spec** axis = the ROADMAP task block (it *is* the originating spec),
   - **Standards** axis = this file (+ the skill's smell baseline).
   Both axes run as parallel fresh sub-agents that see ONLY the diff and the
   spec/standards — **never the implementer's reasoning**. Findings stand on evidence.
3. **Arbitrate.** The orchestrator (or the human) sorts findings: hard violation → fix
   round dispatched to a fresh implementer carrying ONLY the findings + spec; judgement
   call → record in the task's Notes line. Re-review only the fix delta.
4. **Red team at phase gates.** Before each MANUAL-GATE, one fresh agent gets a single
   adversarial charter: *"Break this module: write NEW failing tests (DST edges, malformed
   payloads, boundary sizes, locale traps). If you cannot make it fail, say so
   explicitly."* Tests that encode real behavior join the suite; the rest are documented
   and dropped.
5. `make check` is the objective floor — it never substitutes for review, and review
   findings never override it.

A task is **done** when: DoD ✅, review reports no hard violations, checkbox marked,
Notes log updated.

## Definition of Done (every task)

- [ ] Failing tests were written first and now pass; `make check` green
- [ ] Reviewed per Review protocol — no hard violations (non-DOC tasks)
- [ ] No logic added outside the modules the task names
- [ ] Public functions typed + one-line docstrings
- [ ] ROADMAP.md checkbox updated; commit message references the task ID
- [ ] `proposal.md` untouched unless the task explicitly says so
