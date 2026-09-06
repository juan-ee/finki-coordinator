# ROADMAP.md — Atomic Implementation Queue

**How to use:** work tasks top-to-bottom, one at a time, following the execution protocol
and Definition of Done in `AGENTS.md`. Never start a task whose dependencies are unchecked.
After every non-DOC task, run the **Review protocol** (AGENTS.md) before marking the box.
`DOC` = markdown/config-only task (no failing test required). `MANUAL-GATE` = requires the
Pi/human; the agent only produces/refreshes the verification script.

**Active scope: Phase 2.5 (v7) onward.** Phases 0–2 + the v6.1 follow-ups are complete
and archived **verbatim** (incl. the full Notes log: gate forensics, review
arbitrations, fabrication-episode records) in `docs/roadmap-archive-2026-09-06.md` —
consult it for history; never resume anything there.

| Phase | Exit criteria | Gate |
|---|---|---|
| ~~0–2 + v6.1~~ ✅ | archived — `docs/roadmap-archive-2026-09-06.md` | signed in archive |
| 2.5 v7 restructure | Pi-local KB site + inbox + daily Drive backup | T2.35 manual on Pi |
| 3 Persona | SOUL.md toggle + triage rules + license notices | — |
| 4 Hardening | Backup/restore + runbooks | T4.3 manual on Pi |
| 5 Escalation | Spike doc only (do not implement) | — |

---

## Completed — archived

Phases 0, 1, 2 and the v6.1 follow-ups (T0.1–T2.26) are **done**. Full task texts,
acceptance evidence and the Notes log: `docs/roadmap-archive-2026-09-06.md`.

## Superseded by v7 (proposal §12) — do NOT resume

- **T2.17** `DOC` `MANUAL-GATE` — phase-2 gate doc (v6): historical record. The
  knowledge_sync/search steps never ran and never will (machinery deleted in T2.28).
  Still-live checks ($GAPI credential, Telegram door) fold into T2.35.
- **T2.21** `DOC` — v6.1 gate refresh: target machinery deleted in T2.28; folded into T2.35.
- **T2.24** `OPTIONAL` — Drive Changes API cursor: no watermark exists anymore (T2.28).

---
## Phase 2.5 — v7 knowledge restructure: the Pi is the record (owner decision 2026-09-06)

Full decision: **proposal §12** (owner requirements verbatim, target shape,
invariants, deletions). Short form: all `.md` live in `data/project/docs/`
(git-versioned); the agent writes them directly and reads them back as plain files;
MkDocs Material renders `docs/` to a static site served by caddy and exposed — with
the Hermes dashboard — through one Cloudflare Tunnel behind Cloudflare Access (Google
SSO). Drive is demoted to a daily `knowledge_base/` backup + an `input/`→`processed/`
document inbox. Sync machinery (sync script, syncing.py, knowledge tables,
knowledge_search) is deleted. Invariants: **git safety net · synthesis-vs-copy-pipe
(rule 11) · the 03:00 UTC backup is load-bearing**. Order: T2.27 → T2.28 → T2.29 →
(T2.30 ∥ T2.31) → T2.32 → T2.33 → T2.34 → T2.35.

- [x] **T2.27 `DOC` — fold v7 into the constitution & source of truth** — AGENTS.md:
  mission line (Pi-local record, 8 tools, no sync script), repo map (drop syncing.py +
  sync_knowledge.py), rule 11 reworded as synthesis-vs-copy-pipe with Drive =
  backup + inbox. proposal.md: §1/§2/§3 bodies updated to v7 (§3 retitled, cache DDL
  removed from §1, §12 stays as the changelog), `config.schema.json` drive_root
  description → backup/inbox root. Acceptance: `make check`; grep proves no live doc
  (changelogs/gate history excepted) still claims "Drive is the record" or mentions
  `knowledge_fts`.
- [x] **T2.28 — delete the Drive→cache sync machinery** *(tests first: rewrite the
  affected tests to the v7 shape, watch them fail)* — remove
  `scripts/sync_knowledge.py`, `src/coordinator/syncing.py`, the `knowledge`/
  `knowledge_fts` DDL from `schema.sql`, `KnowledgeRepo`, the `knowledge_search` spec
  + handler + tests (toolset 9 → 8), the T2.23 freshness gate, the `knowledge.*`
  config keys; rewrite `prompts/skills/knowledge/SKILL.md` → file-first authoring and
  reading (`docs/` is the record; ripgrep beats the cache). Acceptance: `make check`;
  the plugin registers exactly 8 tools; grep clean (changelogs/gate history
  excepted).
- [x] **T2.29 — local KB workspace: git + authoring prompts** — `setup.sh` creates
  `data/project/docs/` and `git init`s it; `generate_agents_md.py` documents the
  query map over `docs/`; persona/skills prompts updated: KB changes are written
  under `docs/`, read back with file tools, and Drive is never treated as live
  truth. Tests: setup.sh smoke (docs/ exists, git repo initialized); prompt
  assertions carry the copy-pipe rule.
- [ ] **T2.30 — MkDocs Material site + caddy** — `make site-build` (docker
  `squidfunk/mkdocs-material`, arm64) builds `data/site/` from `data/project/docs/`;
  compose adds a `caddy` file-server service (read-only site mount, bound to
  127.0.0.1:8080); rebuild = host crontab line installed by setup.sh (every 15 min,
  UTC — dumb and LLM-free, rule-11 spirit) plus `make site-build` on demand. Tests:
  compose tests (service, ro mount, loopback bind); a docs fixture builds clean.
- [ ] **T2.31 — cloudflared + dashboard exposure** — compose: `cloudflared` service
  (official arm64 image, token via `.env`; remotely-managed tunnel — hostnames
  kb.* → caddy, board.* → dashboard are configured in the Cloudflare dashboard, not
  in files); env passthrough `HERMES_DASHBOARD` +
  `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`/`_PASSWORD`; `.env.example` lines;
  docker/README: tunnel + Cloudflare Access (Google SSO) click-path, "no inbound
  ports" stays true. Tests: `test_compose.py` pin/env discipline. *(The dashboard is
  Hermes' built-in at the pinned ref — the bundled plugins/kanban dashboard tab was
  verified in the pinned source on 2026-09-06; no new code.)*
- [ ] **T2.32 — daily Drive backup (agent cron, 03:00 UTC)** — skill + hermes cron
  entry: `git -C data/project/docs commit` (invariant 1: commit BEFORE upload), then
  `$GAPI drive upload` of `docs/**` → Drive `knowledge_base/`, with the server-side
  file count reported to the group post. Upload is a CLI file operation (rule 11:
  content never through context). Failure = loud group post + Notes log entry
  (invariant 3). Tests: prompt assertions (commit-before-upload, loud failure);
  plugin cron_relay shape untouched.
- [ ] **T2.33 — inbox flow: Drive `input/` → `processed/`** — skill: on command (or
  daily check), list `input/` via `$GAPI`; per document: download to tmp, READ it
  (synthesis allowed — this is the job), write/merge the new `.md` under `docs/`,
  then `$GAPI move` the original `input/` → `processed/` (metadata op). FORBIDDEN:
  reciting file content into chat as the "update"; hand-copying verbatim
  file-by-file as "sync" (the v6.1 gate failure mode). Tests: prompt assertions for
  the forbidden patterns + the move-after-ingest rule.
- [ ] **T2.34 `DOC` — README.md + KICKOFF.md rewrite for v7** — architecture lines
  (Pi-local record, tunnel diagram), quickstart gains the Cloudflare/Access setup
  pointer, tool count 8, no sync script. Acceptance: a stranger can clone and follow
  the README alone.
- [ ] **T2.35 `DOC` `MANUAL-GATE` — `docs/verify/phase2.5.md`** — Pi drill, human
  sign-off: (1) site reachable via the tunnel hostname; (2) Cloudflare Access blocks
  a non-team Google account (screenshot); (3) dashboard `/kanban` drag-drop works
  behind the gate; (4) the 03:00 backup lands in Drive `knowledge_base/` — file
  count verified in the Drive browser; (5) inbox E2E: drop a doc in `input/`,
  command the agent, a new `.md` appears under `docs/`, the original sits in
  `processed/`; (6) restore drill: rebuild `docs/` from the last snapshot. Absorb
  T2.17's still-live checks ($GAPI credential, Telegram door). Leave unchecked for
  the human.

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
  no Drive copy step (v7 / proposal §12: the primary off-site backup is the agent's
  daily `knowledge_base/` push — T2.32; this tar is the local convenience copy and now
  also carries the journals, whose per-write Drive upload v7 removed); `--dry-run`.
  Tests: `tests/test_backup_smoke.py` —
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


## Notes log (v7 era)

Prior log (T0.1–T2.26 — phase-2 gate forensics, review arbitrations,
fabrication-episode records): `docs/roadmap-archive-2026-09-06.md`.
Log new judgement calls and residuals here, newest first.

- 2026-09-06 (T2.27): DOC task done directly. Scope calls: (a) the proposal **title +
  Goal line** were folded to v7 alongside §1–§3 (a live header claiming Drive-as-source
  would contradict the folded §3); the dated Round-5 D1–D4 block stays as history.
  (b) §5 phases table / §7 resolved-decisions / §9–§12 left untouched (dated history /
  changelogs — the acceptance's "changelogs/gate history excepted"). (c) AGENTS.md repo
  map drops `knowledge.py` (the FTS5 chunker) in addition to the spec's
  `syncing.py` + `sync_knowledge.py` — the chunker is part of the cache indirection §12
  deletes (T2.28 removes the module; the map is the *target* layout). Acceptance grep
  (`grep -rn "Drive is the record\|knowledge_fts" --include=*.md`, excluding ROADMAP
  spec blocks, archive, gate docs, .scratch): after T2.27 the only remaining .md hits
  are `prompts/skills/knowledge/SKILL.md` (rewritten by T2.28) and
  `prompts/persona.md` rule 6 (updated by T2.29) — transitional, each tagged with its
  owning task. Residual for the owner: proposal §6 risk line ("local FTS5 index
  covers text search") and §8 first-boot step 11 (creates the v6.1 knowledge-sync
  cron) still describe v6.1 machinery — unowned by any Phase 2.5 task; recorded, not
  actioned (rule 8).

- 2026-09-06 (T2.28): tests rewritten to the v7 shape FIRST (15 red), then the
  machinery deleted. Judgement calls: (a) `src/coordinator/knowledge.py` (the FTS5
  chunker) is deleted with the cache indirection — its only consumers were the deleted
  sync path and KnowledgeRepo (proposal §12 "the cache/FTS5 indirection is deleted");
  (b) migration 003 is retired and a **migration 004 teardown** drops
  `knowledge`/`knowledge_fts` + the `knowledge_last_freshness_check` stamp on
  existing stores (the cache is not the record; fresh v7 stores record 1, 2, 4);
  (c) the `/opt/data/config` compose mount is removed — it existed only for the T2.23
  freshness TTL knob; (d) the T2.26 incident message in setup.sh reworded (the sync
  script's pre-flight no longer exists); (e) T2.28's grep-clean is scoped to the
  machinery's own surfaces (src/ scripts/ Makefile compose docker/ config/
  knowledge-SKILL plugin.yaml tests/ .env.example) — remaining tracked-file hits are
  the deletion guards + teardown statements themselves, plus the documented
  transitional files (`scripts/generate_agents_md.py` + its test pins and
  `prompts/persona.md` rule 6 → T2.29; README "no FTS5 cache" negation → T2.34
  rewrite). Census: TOOL_SPECS registers exactly 8 tools; suite 286 passed.
  **Review verdicts (fresh dual-axis, fixed point d253f93):** Spec — no hard
  violations; all named deletions verified gone, 8-tool census confirmed; scope-creep
  flags (README truthfulness edits, migration 004 teardown, docker/README jobs note)
  all ruled justified fallout of the deletion and of the grep-clean acceptance.
  Standards — no hard violations; one flag arbitrated as a **judgement call, not a
  hard violation**: persona.md rule 6 / generate_agents_md.py still reference the
  deleted tool under a literal repo-wide reading of "grep clean" — arbitrated on the
  phase's own task order (T2.29 owns persona + generator prompts; editing them here
  would itself be scope creep), with the residual runtime risk (a stale generated
  AGENTS.md naming a deleted tool until T2.29 regenerates it) recorded here and for
  the owner. Small fix applied on the delta: docker/README no longer names the
  not-yet-existing backup skill file (T2.32 lands it). Smell notes accepted as-is:
  the v6.1-shaped test fixture re-declares v6 DDL deliberately (pinned historical
  snapshot for the 004 teardown test).

- 2026-09-06 (T2.29): tests first (12 red), then: setup.sh step 7 git-inits
  `data/project/docs/`, generator v7 sections (docs/ query map, synthesis-vs-copy-pipe
  in the rendered policy), persona rules 3/6 + checklist flipped to file-first,
  project-template/README.md rewritten. Judgement calls: (a) **git identity policy** —
  a LOCAL identity is set only when the operator has none (never overrides a global
  one), and the baseline commit happens on first boot only; re-runs never commit
  operator changes (that is the T2.32 backup job's commit, not setup.sh's);
  (b) `project-template/README.md` is rewritten in this task (it is the workspace's
  own authoring doc — the "local KB workspace" surface — rather than T2.34's repo
  README/KICKOFF); (c) the seed-snapshot test now excludes `.git` internals (runtime
  VCS state, not seeded content — the never-overwrite guarantee is still asserted for
  every real file); (d) the fixture operator identity is injected through BOTH
  GIT_CONFIG_GLOBAL and $HOME/.gitconfig (git-version portability).
  **Review verdicts (fresh dual-axis, fixed point f0724c6):** Spec — one hard-class
  finding, FIXED in-round: the digest skill still taught "the Drive copy is the
  team's record" + a per-write $GAPI upload (step 6) — exactly the claim this task's
  "Drive is never treated as live truth" retires; step 6 now says the journal stays
  local and rides the daily 03:00 UTC backup (T2.32 lands the backup skill + cron;
  its loud-failure guidance moves there), plus a test_prompts_v7 assertion pinning
  the digest skill. Also fixed: the placeholder-guard tautology in
  test_project_template_readme (Spec C1) and a weak "git" output assertion.
  Standards — no hard violations; project-template/README scope item ruled a
  disclosed judgement call (Notes (b)); the near-verbatim copy-pipe wording across
  persona/generator/README accepted (distinct audiences; drift tracked here) and the
  setup.sh extract-function suggestion noted, not taken (linear first-boot
  sequence).
