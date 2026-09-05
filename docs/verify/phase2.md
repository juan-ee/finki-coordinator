# Phase 2 verification — member lifecycle & knowledge on the Pi (MANUAL GATE, v6)

> **Human-only.** A coding agent cannot run this gate: it needs the Raspberry Pi, the
> live Hermes container, the agent's google-workspace skill (`$GAPI`) against the real
> Drive, and a human editing Drive in a browser. Work top to bottom; tick each `[ ]`
> as it passes; do not skip failures.
>
> **v6.1 resumption note (2026-09-05, mid-gate design change):** this gate sanctioned
> making knowledge sync deterministic — `scripts/sync_knowledge.py` (hermes cron +
> `make sync`) replaces the agent-mediated two-call `knowledge_sync` tool, which was
> caught inventing file contents and stalling batching during step 2 below. Spec:
> proposal §11 (D2 rev) + ROADMAP T2.18–T2.22 (AGENTS.md hard rule 11). Effects when
> those tasks land: pre-flight tool count 10 → 9 (no `knowledge_sync` tool); step 2's
> round-trip + no-op are re-verified against the script (the 2026-09-05 agent-run
> evidence below stays as the incident record); step 3 unchanged (read path);
> steps 5–9 as written. **Do not tick remaining knowledge boxes until T2.18–T2.20
> ship.**
>
> **STOP CONDITION (read before anything else):** step 1 verifies `$GAPI` works
> in-container at our pinned `HERMES_REF`. If it does **not**, the phase **stops
> there**: record the exact error + evidence, untick nothing, and report to the owner.
> **No workarounds exist by design** — no rclone, no service accounts, no dependency
> installs. The whole v6 knowledge loop rides on this one skill.

**What phase 2 shipped** (the thing being verified, T2.5–T2.16): door-first onboarding
(`scripts/allow.sh`, `member_add` with required `telegram_id`, owner-only
`member_delete`), seed hygiene, the dead-weight cut (`status_days`, `digest_time`,
compose Drive env, `sync.sh`), the knowledge subsystem (v6 schema +
`knowledge_sync`/`knowledge_search` on an FTS5 cache), the digest uploading instead of
syncing, and the persona/skills v6 guidance. Deliberately NOT in this gate: backups
(phase 4) and vector RAG (phase 5, forbidden until the team decides).

## Pre-flight

- [ ] Pi repo pulled to the shipped phase-2 commit (`git log --oneline -1`; record:
      ____________). Working tree clean (`git status`).
- [ ] Committed seed carries placeholder IDs only:
      `git show HEAD:config/members.seed.yaml | grep telegram_id` → every value
      `null`. (The Pi's local `data/hermes/hermes-coord.db` may still hold real IDs —
      that is runtime state, not the committed seed.)
      **✓ 2026-09-05 (agent-run @ fcd398a):** all seed rows `telegram_id: null`.
- [x] `.env` on the Pi contains Telegram + OpenRouter (+ `HERMES_UID/GID`) and **no**
      `GOOGLE_DRIVE_*`/`RCLONE_*` lines (`grep -E 'GOOGLE_DRIVE|RCLONE' .env` → empty).
      **✓ 2026-09-05 (agent-run):** 6 dead v5 lines (5 active + 1 commented) removed,
      backup on Pi: `.env.bak-20260905-095236`; grep now → 0; `TELEGRAM_*`,
      `OPENROUTER_API_KEY`, `HERMES_UID=1000`/`HERMES_GID=1000` present. Container
      untouched (compose never passed those vars — no restart needed).
- [x] Container is up and carries the new plugin: ask the bot in a DM to list its
      coordinator tools → **10** tools incl. `member_delete`, `knowledge_sync`,
      `knowledge_search`.
      **✓ 2026-09-05:** bot DM listed exactly the 10 (member_list/add/update/delete,
      checkin_submit, checkins_by_date, knowledge_sync, knowledge_search,
      setting_get/set) — matches the static TOOL_SPECS registry; `hermes plugins
      list` shows `coordinator` enabled (source: user).

## 1. $GAPI works in-container — THE FIRST ITEM (STOP if it fails)

- [x] In a DM, ask the bot: *"List the files in our Drive root using your
      google-workspace skill."* → the agent invokes `$GAPI` and returns a real file
      listing from the team's Drive.
      **✓ 2026-09-05 (after OAuth resolution above):** bot returned a real listing
      (24 items, 10 folders + files) via $GAPI. Caveat: the listing was the
      OWNER'S WHOLE account root (incl. personal folders), not the team folder —
      see the Drive-scoping deviation below; the $GAPI capability itself passes.
      **✓ 2026-09-05 (re-verified on the dedicated bot account):** DM re-run →
      agent honestly reported its own Drive root as EMPTY (by design) and found
      exactly the two team files shared into Fink-Labs (Fink-Labs Logo.html,
      Arquitectura_Piloto.md — owned by juanque.4). No personal content visible.
      Friction noted: Hermes' Tirith command-approval gate required 2 operator
      approvals for the agent's shell calls (expected; approved). Bot offered to
      "re-auth with the right account" after seeing the empty root — declined:
      finklabs2026 is the intended isolation setup.

      **✗ 2026-09-05 — STOP CONDITION TRIGGERED (phase halted at this step):**
      - Bot DM reply (gist, verbatim key): **NOT_AUTHENTICATED** — "no existe token
        de Google en esta máquina, nunca se completó el OAuth"; cannot list the
        Drive root without it. Bot requested the client-secret JSON + browser
        approval (the skill's own first-time setup).
      - Skill's own triage command, run in-container:
        `python /opt/data/skills/productivity/google-workspace/scripts/setup.py --check`
        → `NOT_AUTHENTICATED: No token at /opt/data/google_token.json` (exit 1).
      - Credential files: `~/.hermes/google_client_secret.json` and
        `~/.hermes/google_token.json` both ABSENT (host `~/.hermes` = `/opt/data`
        in-container). `~/.hermes/auth.json` holds only an OpenRouter credential —
        no Google credential exists anywhere on the Pi.
      - Skill state: `google-workspace` builtin skill **enabled** (healthy) — it is
        the credential that is missing, not the skill.
      - `hermes --version`: Hermes Agent v0.21.0 (2026.8.31), install method docker,
        Python 3.13.5 — container built at pinned ref 29112bef.
      - Agent logs: no google/oauth lines in the 3h of gateway logs around the DM.
      - Steps 2–9 NOT run (stop rule). No workaround attempted, per this page.
      - **Owner action — the skill's designed first-time setup (its SKILL.md, not a
        workaround):** (1) GCP console → enable Drive API + Docs API (step 9 needs
        export), create OAuth 2.0 Client ID, type **Desktop app**, add the owner
        account as test user; (2) get `client_secret_*.json` onto the Pi and run
        in-container `setup.py --client-secret <path>`; (3) `setup.py --auth-url
        --services drive,docs --format json` → open `auth_url` in a browser,
        approve (the `http://localhost:1` failure afterwards is expected), copy the
        full redirected URL; (4) `setup.py --auth-code "<pasted URL>"` then
        `setup.py --check` → must print AUTHENTICATED. Then re-run THIS step and
        resume the gate top-to-bottom.

      **↻ RESOLVED same day (2026-09-05):** the OAuth was completed through the
      skill's own `setup.py` flow — no workaround. Client credentials reused from
      the retired v5 rclone config (found in the pre-cleanup `.env` backup
      `.env.bak-20260905-095236`), written as a Desktop-app
      `installed`-format JSON to `~/.hermes/google_client_secret.json` (600).
      PKCE `--auth-url` → browser consent → `--auth-code` exchange:
      `OK: Authenticated. Token saved to /opt/data/google_token.json`;
      `setup.py --check` → `AUTHENTICATED` (exit 0). **Scope note:** consent
      granted `drive` only — 7 other scopes (gmail/calendar/documents/contacts/
      spreadsheets) unticked and still missing. Sufficient for this gate (all
      steps are Drive-scoped; step 9's PDF extraction rides on Drive download);
      top-up consent can be run later via a fresh `--auth-url` pass. STOP lifted;
      gate resumes at step 1.

**If this box cannot be ticked:** STOP the entire phase here. Record on this page:
`hermes --version` output, the exact agent/tool error, and anything the agent logs.
Report to the owner. Do NOT attempt rclone, service accounts, manual OAuth, or any
other fallback — the v6 design has none on purpose (proposal §6.9).

## 2. knowledge_sync round-trip — DOWN (Drive → cache)

- [x] **Test data first (Drive web UI):** in the Drive root create/upload 2 small
      markdown docs — `docs/notes/decisión.md` containing the word **decisión** and a
      sentence with a distinctive phrase (record it: ____________), and
      `docs/notes/howto-test.md` with a second distinctive phrase (record it:
      ____________).
      **✓ 2026-09-05 (adapted to the new knowledge-base shape — see Drive-scoping
      deviation):** knowledge base = the bot account's own Drive; test docs live in
      its `notes/` folder. `notes/decisión.md` (id 1PqxE7…, uploaded server-side via
      the skill, owner-approved approach) contains the word **decisión**; distinctive
      phrase recorded: **"el quokka baila tangos los martes"**. Second distinctive
      phrase: **"be happy everyday"** (in `notes/decision.md`, owner-created). Both
      verified via `drive search`/download in-container.
- [ ] DM: *"Sync the knowledge base."* → the agent runs the plan call (watermark),
      lists/selects/downloads via $GAPI, then ingests. The tool result reports
      `synced: N` (N ≥ 2) and a watermark value. Record: ____________.
      **◐ 2026-09-05 — sync executed; end state VERIFIED server-side; incident
      recorded:** the agent downloaded 19 files, made one mid-run mistake (passed
      invented placeholder summaries — cache poisoning attempt), CAUGHT ITSELF, and
      re-ingested with real content via the same-file_id-replaces contract. Final DB
      state (operator-verified): 99 chunks / 14 files (root + notes/ + old ideas/,
      PDF and Logo title/path-only), zero placeholder markers, watermark
      2026-09-05T09:25:35Z; FTS `decision` matches `notes/decisión.md`
      (accent-insensitive ✓). Minor observation: a few stored modified_time values
      carry a 9h skew vs Drive mtime (agent passed offset forms; handler normalized
      them) — harmless, may cause one extra re-download next sync. **Design verdict
      recorded:** the agent-mediated sync (LLM shuttles file contents through tool
      calls in batches) is slow and hallucination-prone — POST-GATE TASK: add a
      deterministic sync script using the plugin's own chunker/repository + gws CLI
      download, wired to hermes cron (restores on-demand AND automatic sync without
      an LLM in the data path). Second no-op sync pending while the agent's repair
      run settles. **Final agent report (2026-09-05, run completed):** 19 files / 165
      chunks / watermark 2026-09-05T09:25:35Z — operator-verified in the DB (19
      distinct paths AND file_ids; marker scan clean — two apparent "placeholder"
      hits are false positives: the Spanish customs term "ad-valorem" in
      Estudio_Mercado_Limpieza_Laser_Ecuador.md). Bot disclosed the placeholder
      incident unprompted; PDF + Logo correctly title/path-only.
- [ ] **Second sync is a no-op:** DM *"Sync the knowledge base"* again → the agent
      lists, finds nothing past the watermark, and reports nothing new ingested
      (no duplicate storage).

## 3. knowledge_search — diacritics + live confirmation (READ)

- [ ] DM: *"Search the knowledge base for decision."* → the hit list includes the
      `decisión.md` document (accent-insensitive MATCH on real data).
- [ ] DM: *"What exactly does that document say?"* → the agent **reads the live Drive
      original via $GAPI** and quotes it (persona rule 6: the index is a finding aid).
- [ ] DM: *"Search the knowledge base for ____________"* (the second doc's phrase) →
      found, correct document identified.

## 4. FTS5 integrity-check (INDEX health)

- [x] With the bot idle, run in-container:

```sh
docker compose exec gateway python3 -c "import sqlite3; c = sqlite3.connect('/opt/data/workspace/hermes/hermes-coord.db'); c.execute(\"INSERT INTO knowledge_fts(knowledge_fts, rank) VALUES('integrity-check', -1)\")"
```

      exits 0 (clean). **The rank form is required** — the plain
      `VALUES('integrity-check')` verifies only FTS-internal structure and does NOT
      compare against the external content table (pinned on SQLite 3.50.4, T2.13).
      **✓ 2026-09-05 (agent-run in-container):** exit 0, clean. Note: container SQLite
      is 3.53.4 (the "3.50.4" pin note above is stale — rank form still required/valid).

## 5. UP path — upload after write (cache → Drive)

- [ ] DM: *"Write a short journal note for today and make sure it reaches Drive."* →
      the agent writes `journal/YYYY-MM-DD.md` locally and uploads it via
      `$GAPI drive upload`. Verify in the **Drive web UI**: the file exists with
      today's content. Record the Drive path: ____________.
- [ ] **DOWN proof of the round trip:** in the Drive web UI, append a line containing
      a third distinctive phrase (record: ____________) to the uploaded note → DM
      *"Sync the knowledge base"* → DM *"Search the knowledge base for ____________"*
      → the agent finds it and confirms against the live file.

## 6. Digest end-to-end

- [ ] At the digest time (or after conversationally creating/adjusting a test digest
      job for a near time — restore it afterwards): the digest runs, writes the
      journal entry, **uploads it to Drive** (visible in the browser), and posts the
      group summary.

## 7. Runtime AGENTS.md reflects v6

- [ ] Ask the bot: *"What is your query map?"* → the answer matches the v6 map
      (mission → Drive brief; questions → `knowledge_search` then live read; tasks →
      kanban; who → `member_list`; **no** `search_files`-under-docs, **no** mirror).
- [ ] If the roster changed since the last render:
      `uv run python scripts/generate_agents_md.py --db data/hermes/hermes-coord.db`
      (venv python on the Pi) regenerates deterministically.

## 8. Member lifecycle spot-checks (door-first flow, live)

- [ ] **Door:** pick a REAL new member (or a teammate's unused ID). They send their ID
      to the owner → owner runs `./scripts/allow.sh <id>` → output names the added ID,
      shows `docker compose up -d` (never restart), and the container is recreated.
      Second run with the same ID: no-op.
- [ ] **Complete row:** the new member DMs *"I'm <name> — <role>, <city>, wake HH:MM"*
      → the agent calls `member_add` **with their telegram_id from session context**
      (a complete row — no ID-less adds) and relays the create cronjob call verbatim.
- [ ] **Duplicate names:** ask the bot to add a member whose name already exists on
      the active roster → rejected with a summary pointing at `member_update`.
- [ ] **member_delete (owner-only):** ask the bot to remove a test member → row and
      check-ins gone, and the check-in cron job removed (`docker compose exec gateway
      hermes cron list` no longer shows `checkin-<id>`). Non-owner DMs asking for
      removal are declined and routed to the owner (persona rule 4).
- [ ] Cleanup: remove any throwaway rows via `member_delete` (never raw SQL) and
      restore `TELEGRAM_ALLOWED_USERS` in `.env` by hand if a test ID must go (manual,
      then `docker compose up -d` — allow.sh only appends).

## 9. Doc-extraction check ($GAPI export, non-text rule)

- [ ] Upload a small PDF containing one distinctive nonsense phrase into the Drive
      `docs/` tree → DM: *"Sync the knowledge base"*, then *"Search the knowledge base
      for <the PDF's title>"* → found (title/path indexed). DM: *"What does the PDF
      say?"* → the agent extracts the text via $GAPI **on live read** and returns the
      phrase. (The cache holds no PDF text — that is the second-pass rule working.)
- [ ] Cleanup: remove the PDF or move it to the Drive archive.

## Sign-off

- [ ] All boxes above ticked (no unticked box may remain — step 1 is the STOP gate).
- [ ] Date / Pi OS version / Hermes ref: ____________ / ____________ / 29112bef
      (v2026.8.31, Hermes v0.21.0)
- [ ] $GAPI verification evidence attached or referenced: ____________
- [ ] Operator signature: ____________ (chat-signed ____________)

Deviations observed (if any):

- 2026-09-05: **DRIVE SCOPING GAP (decision: dedicated bot account — in progress):**
  (a) OAuth was completed with the owner's PERSONAL Google account, so $GAPI can
  read/list the entire account (personal folders visible; proposal §README line
  458 assumed "the bot's Drive account" reaching the *team's* Shared Drive).
  (b) Template gap: `project.drive_root` (config: "Fink-Labs") is parsed by
  `config.py` but never used — the knowledge_sync plan hint and the knowledge
  skill both say "list the Drive root", and the generated AGENTS.md never names
  the team folder. Nothing pins the agent to Fink-Labs. Cache still empty
  (0 rows) — nothing personal ingested yet. **Decision (2026-09-05): dedicated
  bot account** `finklabs2026@gmail.com` — Fink-Labs shared to it (Editor), GCP
  test user added, personal-account token revoked (`--revoke`), fresh OAuth with
  drive scope only. Isolation verified in-container: `drive search "Fink-Labs"`
  → folder found; `drive search "Marriage"` → `[]`. Scope pointer added to
  runtime AGENTS.md. Template follow-up still open: wire `drive_root` into the
  knowledge_sync work order + skill prompt (post-gate task). **Live proof of the
  gap (2026-09-05):** asked to "list the Drive root", the agent surfaced only 2
  of the 8 Fink-Labs items — it obeyed the root instruction (empty), then fell
  back to full-text drive search (matches names/content, not a folder listing)
  and never enumerated the folder's children. Confirms the work order must carry
  the folder id + a children query. **Approach changed same day (owner
  decision):** the shared-folder model was abandoned — owner revoked the
  Fink-Labs share and made the bot account's own My Drive THE knowledge base
  (team files at its root + notes/ + old ideas/). "List the Drive root" now
  natively points at the team space; the `drive_root` wiring follow-up remains
  open for the template.
- 2026-09-05: **STEP-1 STOP** — `$GAPI` unauthenticated in-container (OAuth never
  completed; no Google credential on the Pi). Full evidence + owner action plan
  recorded under step 1. Phase halted there; steps 2–9 not run. **RESOLVED same
  day** via the skill's own OAuth setup (drive scope; details under step 1) —
  gate resumed.
- 2026-09-05: Pi working tree not clean — untracked `config/config.yaml` (real
  runtime config; the template ships only `config.example.yaml` and does not
  gitignore the real one). Proposed post-gate template fix: add
  `config/config.yaml` to `.gitignore`. (Also affects the stranger test.)
- 2026-09-05: runtime `data/project/AGENTS.md` was stale v5 (`search_files` under
  docs/, rclone bisync mirror wording). Regenerated during validation via
  `uv run python scripts/generate_agents_md.py --db data/hermes/hermes-coord.db`
  → v6 map confirmed in the file (bot re-ask still pending, step 7).
- 2026-09-05: in-container SQLite is 3.53.4; step-4's "pinned 3.50.4" note is
  stale (rank-form check unaffected).
- 2026-09-05: `.env` carried 6 dead v5 `GOOGLE_DRIVE_*`/`RCLONE_*` lines; removed
  (backup `.env.bak-20260905-095236`) — see pre-flight box 3 evidence.
- 2026-09-05: roster legacy rows Jose/Luis/David have `telegram_id: null` (pre-v6;
  only Juan onboarded under v6). Context for step 8 duplicate-name/delete tests.
- 2026-09-05: no digest cron job exists yet (`hermes cron list` shows only
  `checkin-1`) — step 6 will use the conversational test job, per the doc.

> After sign-off: tick the T2.17 box in ROADMAP.md, add the evidence Notes log entry,
> and commit `chore(T2.17): phase-2 gate signed off`. Phase 2 exit criteria are then
> met — and the stranger test (fresh clone, README only, bot boots) should be re-run
> on a scratch clone before announcing v6.
