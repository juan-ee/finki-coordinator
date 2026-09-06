# Phase 2 verification — member lifecycle & knowledge on the Pi (MANUAL GATE, v6.1)

> **Human-only.** A coding agent cannot run this gate: it needs the Raspberry Pi, the
> live Hermes container, the agent's google-workspace skill (`$GAPI`) against the real
> Drive, and a human editing Drive in a browser. Work top to bottom; tick each `[ ]`
> as it passes; do not skip failures.
>
> **STOP CONDITION (read before anything else):** step 1 verifies `$GAPI` works
> in-container at our pinned `HERMES_REF`. If it does **not**, the phase **stops
> there**: record the exact error + evidence, untick nothing, and report to the owner.
> **No workarounds exist by design** — no rclone, no service accounts, no dependency
> installs. The whole v6 knowledge loop rides on this one skill.

**What phase 2 shipped** (the thing being verified, T2.5–T2.16 plus the v6.1 follow-ups
T2.18–T2.23): door-first onboarding (`scripts/allow.sh`, `member_add` with required
`telegram_id`, owner-only `member_delete`), seed hygiene, the dead-weight cut
(`status_days`, `digest_time`, compose Drive env, `sync.sh`), the knowledge subsystem
(v6 schema; `knowledge_search` on an FTS5 cache refreshed by the deterministic
`scripts/sync_knowledge.py` — hermes cron + `make sync`, no LLM in the data path,
AGENTS.md hard rule 11 — plus the read-through freshness gate on search, T2.23), the
digest uploading instead of syncing, and the persona/skills guidance. Deliberately NOT
in this gate: backups (phase 4) and vector RAG (phase 5, forbidden until the team
decides).

## Pre-flight

- [x] Pi repo pulled to the shipped phase-2 commit (`git log --oneline -1`; record:
      ____________). Working tree clean (`git status`).
      **✓ 2026-09-05 (agent-run over SSH):** Pi at `3577477` (the T2.21 gate
      refresh); tree clean except the operator's own `.env.bak-20260905-095236`
      backup artifact (recorded in the T2.22 notes).
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
      list` shows `coordinator` enabled (source: user). *(v6 evidence — superseded by
      the v6.1 re-check below.)*
- [x] **v6.1 re-check (after T2.18–T2.23):** the container carries the 9-tool plugin —
      `git pull` + `docker compose up -d` first (bind mounts apply only at container
      creation), then ask the bot in a DM to list its coordinator tools → exactly
      **9** (`member_add`, `member_update`, `member_list`, `member_delete`,
      `checkin_submit`, `checkins_by_date`, `knowledge_search`, `setting_get`,
      `setting_set`) and **no `knowledge_sync`**. Record: ____________.
      **✓ 2026-09-05:** bot DM listed exactly the 9, grouped
      roster/check-ins/settings/knowledge — matches the headless census (agent-run
      in-container: count 9, `knowledge_sync present: False`).

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
      **RUN THE OAUTH AS THE RUNTIME USER, NEVER root/sudo (T2.26):** every
      in-container `setup.py` / gws CLI command goes through
      `docker compose exec --user 1000 gateway …` (the container's `HERMES_UID`).
      Bare `docker compose exec` defaults to root, and a root-run OAuth saves
      `google_token.json` root-owned — every later refresh write by uid 1000 then
      fails (the 2026-09-05 step-5 incident). setup.sh step 8/8 now checks and
      repairs exactly this, and the sync script's pre-flight fails loudly on it.

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

## 2. Knowledge sync round-trip — DOWN (Drive → cache) — v6.1: the deterministic script

The cache is refreshed by `scripts/sync_knowledge.py` — host: `make sync`; in-container
one-shot: `python3 /opt/data/scripts/sync_knowledge.py` with the container's default
`python3` — the Hermes venv, `/opt/hermes/.venv/bin/python3` (the T2.23 freshness-gate
subprocess form: ONE interpreter carries the coordinator imports — the script adds
`/opt/data/plugins` itself — AND the CLI's googleapiclient; never compose PATHs to
`/opt/data/venvs/gapi` or use `/usr/bin/python3`, which lacks googleapiclient — the
split pair is what made the 2026-09-05 one-shot look "not self-sufficient"). Bulk syncs
are never agent-mediated (AGENTS.md hard rule 11) — the agent-mediated run below is
kept as the incident record that motivated the switch (proposal §11); the agent's ONE
sanctioned run is the post-upload write-through one-shot (step 5, pinned in the
knowledge skill).

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
- [x] **Incident record — v6 agent-mediated run (superseded by the script below, kept
      as evidence):** the box was to DM *"Sync the knowledge base"* → the agent runs
      the plan call (watermark), lists/selects/downloads via $GAPI, then ingests; the
      tool result reports `synced: N` (N ≥ 2) and a watermark value.
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
- [x] **First script round (real, ingests):** upload a fresh small markdown doc (or
      append a line to an existing one) in the Drive web UI, then on the Pi host run
      `make sync` → exit 0; the report ingests exactly the changed file(s) and prints
      the post-round watermark. Record: ____________.
      **✓ 2026-09-05 (operator edited `Agencia_Automatizacion_IA_PYMES_Ecuador_
      CONSOLIDADO.md` in the Drive web UI; agent-run `make sync` over SSH):**
      `1 text + 0 index-only selected, 18 unchanged` → ingested 1, failed 0,
      watermark → `2026-09-05T19:13:54Z`. Cache after: 19 files / 166 chunks
      (the doc re-indexed at 8 chunks).
- [x] **Second script round is a no-op:** `make sync` again with no Drive-side changes
      → 0 ingested (every file unchanged), exit 0, watermark unchanged. Record:
      ____________.
      **✓ 2026-09-05 (agent-run):** `0 selected, 19 unchanged` → ingested 0,
      failed 0, watermark unchanged (`2026-09-05T19:13:54Z`), exit 0.

## 3. knowledge_search — diacritics + live confirmation (READ)

> v6.1 read path (T2.23): a search whose last freshness check is older than
> `knowledge.freshness_ttl_minutes` (config knob, default 10) runs a quick incremental
> script sync first and says so in the result summary; searches inside the TTL are
> served straight from the cache. Degraded mode: Drive unreachable → the cache is
> served and the summary says so — reading never hard-fails.

- [x] DM: *"Search the knowledge base for decision."* → the hit list includes the
      `decisión.md` document (accent-insensitive MATCH on real data).
      **✓ 2026-09-05:** 10 hits incl. `notes/decisión.md` and the freshly re-indexed
      CONSOLIDADO doc; the bot reported "cache refreshed just now, 0 failures" —
      the T2.23 freshness gate fired (stamp `13:55:35Z` → `19:19:11Z`,
      operator-verified in settings).
- [x] DM: *"What exactly does that document say?"* → the agent **reads the live Drive
      original via $GAPI** and quotes it (persona rule 6: the index is a finding aid).
      **✗ 2026-09-05 — FAIL as run:** the agent quoted from `~/.hermes/kb_sync/` and
      only *offered* to pull live originals afterwards — the designed order is
      live-read BEFORE quoting. Root cause (see deviations): a runtime-only,
      agent-authored skill (`~/.hermes/skills/coordinator-operations/`) tells the bot
      "a synced copy of the cache lives under `~/kb_sync/`"; the runtime SOUL.md is
      also stale (predates persona rule 6). Re-run the ask after the runtime hygiene
      fix closes the box.
      **↻ RE-RUN 2026-09-05 (fresh DM session, after the fix bundle) — PASS:**
      (1) asked for `notes/decisión.md` as a bare path, the bot searched its LOCAL
      workspace, found nothing, and honestly reported the absence with a provenance
      question — no stale quote (the kb_sync trap is gone);
      (2) asked via the knowledge base, the bot called `knowledge_search`, then read
      the LIVE Drive originals of BOTH docs via $GAPI and quoted them — content
      matches the recorded test phrases exactly ("el quokka baila tangos los
      martes" / "be happy everyday"), and the bot stated the real content comes from
      the Drive originals. Freshness stamp moved 19:19:11Z → 19:43:55Z (TTL elapsed;
      the read-gate refresh fired during the search, operator-verified in settings).
      Friction noted: the fresh session re-derived the $GAPI invocation via terminal
      probes (the removed curator skill had hardcoded it) — acceptable; T2.25 may
      add a pointer. On-Pi debounce was not demonstrated live (every search this
      session hit an elapsed TTL); the debounce remains covered by the T2.23 suite.
- [x] DM: *"Search the knowledge base for ____________"* (the second doc's phrase) →
      found, correct document identified.
      **✓ 2026-09-05:** "be happy everyday" → 1 hit, `notes/decision.md`.

## 4. FTS5 integrity-check (INDEX health)

- [x] With the bot idle, run in-container:

```sh
docker compose exec gateway python3 -c "import sqlite3; c = sqlite3.connect('/opt/data/workspace/hermes/hermes-coord.db'); c.execute(\"INSERT INTO knowledge_fts(knowledge_fts, rank) VALUES('integrity-check', -1)\")"
```

      exits 0 (clean). **The rank form is required** — the plain
      `VALUES('integrity-check')` verifies only FTS-internal structure and does NOT
      compare against the external content table (verified on the suite's SQLite
      3.50.4 and the container's 3.53.4, T2.13 + T2.22).
      **✓ 2026-09-05 (agent-run in-container):** exit 0, clean.

## 5. UP path — upload after write (cache → Drive)

- [ ] DM: *"Write a short journal note for today and make sure it reaches Drive."* →
      the agent writes `journal/YYYY-MM-DD.md` locally and uploads it via
      `$GAPI drive upload`, then runs the sync one-shot (mandatory write-through:
      `python3 /opt/data/scripts/sync_knowledge.py` under the container's default
      Hermes-venv `python3` — the pinned T2.23 subprocess form, see step 2 — the
      transcript must show the counts line). Verify in the **Drive web UI**: the
      file exists with today's content, and a DM search finds it immediately.
      Record the Drive path: ____________.
      **✗ 2026-09-05 — FAIL as run (3 defects):**
      (1) local write OK (`data/project/journal/2026-09-05.md`, 164 bytes);
      (2) upload landed in the DRIVE ROOT as `journal-2026-09-05.md` — no `journal/`
      folder exists on Drive; the "matching Drive folder" destination was never
      established anywhere (template gap the bot filled with a guess);
      (3) the MANDATORY write-through one-shot never ran — no counts line; cache
      verified 0 journal rows afterwards.
      (Content note: not a fabrication — the operator's ask instructed "puedes decir
      que hoy me reuní con José y David"; the bot obeyed faithfully.)
      Frictions: 2+ Tirith HIGH approvals on the `GAPI=…; $GAPI …` pattern (~3 min,
      iteration 12/500); the agent installed Google API deps into a new venv
      (`/opt/data/venvs/gapi`) on its own initiative (runtime mutation — benign
      outcome, uncontrolled mechanism). Re-run (Spanish ask, single message bundling
      append + upload-to-folder + one-shot) after the Drive-side `journal/` folder
      convention is settled — see deviations.
      **↻ RE-RUN 2026-09-05 (after the operator created `journal/` on Drive and moved
      the file) — PASS, with heavy caveats:** the bot appended the operator-supplied
      line, uploaded to `journal/2026-09-05.md` (id 1PU7hP5y…, verified by reading
      the content back from Drive), and ran the write-through one-shot — the counts
      line ON CAMERA: `1 text selected … ingested 1 file(s); failed 0; watermark
      2026-09-05T20:12:12Z`. Cache independently verified (row present, line in
      body, watermark matches). Caveats recorded in the deviations: the Google
      token was root-owned mid-run (the bot worked around it with a manually
      refreshed access token in a shadow HERMES_HOME and a direct-API upload
      bypassing the CLI), and the documented in-container one-shot invocation is
      not self-sufficient (dual-interpreter problem) — the bot composed PATHs to
      make it work. Both feed T2.26.
- [x] **DOWN proof of the round trip:** in the Drive web UI, append a line containing
      a third distinctive phrase (record: ____________) to the uploaded note → the
      refresh happens through the SYSTEM's own path: wait out the freshness TTL
      (10 min) and DM the search — the T2.23 read gate runs the deterministic sync
      first, and the summary says so → the agent finds the phrase and confirms
      against the live file. (`make sync` on the Pi host is the operator fallback,
      not the primary path — the v6.1 design makes the read path self-healing.)
      **Phrase appended ("Nueva linea al final"); attempt 1 ✗ (2026-09-05, fresh
      session):** the bot went FILESYSTEM-diving for the phrase (disk search + past
      conversations) instead of calling `knowledge_search` — no tool call, no
      freshness gate, no FTS. Consequence of the known gap: our repo skills are
      not installed in the runtime, the curator-authored workflow skill was
      removed, and the fresh session had no workflow guidance mapping a
      knowledge-base ask to the tool (SOUL.md rule 6 alone didn't carry it).
      Re-ask with the tool named explicitly to close the box.
      **↻ Attempt 3 (operator named the sync mental model) — PASS:** the bot called
      `knowledge_search`; the T2.23 freshness gate fired (TTL long elapsed) — the
      deterministic sync ingested the edited journal (ingested 1, watermark
      20:16Z, reported in the reply) — the FTS hit `journal/2026-09-05.md`, and
      the bot confirmed the phrase against the LIVE Drive original before quoting.
      The DOWN proof ran through the system's own path with ZERO manual commands —
      the designed v6.1 behavior. Honest caveat: it took operator teaching to get
      there (attempt 1 disk-dived; attempt 2 clarified the scope was local-only) —
      the repo-skills-not-installed gap is the root cause (T2.25).

## 6. Digest end-to-end

- [ ] At the digest time (or after conversationally creating/adjusting a test digest
      job for a near time — restore it afterwards): the digest runs, writes the
      journal entry, **uploads it to Drive** (visible in the browser) and runs the
      write-through sync (the same pinned one-shot as step 5:
      `python3 /opt/data/scripts/sync_knowledge.py` on the container's default
      Hermes-venv `python3` — the counts line must appear in the job output), and
      posts the group summary. An upload failure is reported in one line at the end
      of the journal entry — drift must be visible, not silent.

## 7. Runtime AGENTS.md reflects v6

- [x] Ask the bot: *"What is your query map?"* → the answer matches the v6 map
      (mission → Drive brief; questions → `knowledge_search` then live read; tasks →
      kanban; who → `member_list`; **no** `search_files`-under-docs, **no** mirror).
      **✓ 2026-09-05:** all five v6 entries correct (mission → `docs/product/brief.md`
      on Drive read before big decisions; questions → `knowledge_search` + live
      confirm; status → `journal/` + `checkins_by_date`; tasks → `kanban_*` tools,
      never files; who → `member_list`, never a file) plus two coherent extras
      (inbox/ triage, templates/) drawn from the generated AGENTS.md; the two
      cross-cutting rules stated unprompted (Drive is the record / DB is the roster
      truth; schedules via tools so relays regenerate — never by hand). No
      `search_files`, no mirror.
- [ ] If the roster changed since the last render:
      `uv run python scripts/generate_agents_md.py --db data/hermes/hermes-coord.db`
      (venv python on the Pi) regenerates deterministically.
      **✓ 2026-09-05 (agent-run):** regenerated even though the roster did not
      change — the T2.20 generator rewrite (deterministic-script wording) postdates
      the previous render (11:26Z, still said `knowledge_sync`); re-render → v6.1
      text, tree untouched. The query-map DM ask remains for the human (box above).

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
      `docs/` tree → run `make sync` on the Pi host, then DM *"Search the knowledge
      base for <the PDF's title>"* → found (title/path indexed). DM: *"What does the
      PDF say?"* → the agent extracts the text via $GAPI **on live read** and returns
      the phrase. (The cache holds no PDF text — that is the second-pass rule working.)
- [ ] Cleanup: remove the PDF or move it to the Drive archive.

## Sign-off

- [ ] All boxes above ticked (no unticked box may remain — step 1 is the STOP gate).
- [ ] Date / Pi OS version / Hermes ref: ____________ / ____________ / 29112bef
      (v2026.8.31, Hermes v0.21.0)
- [ ] $GAPI verification evidence attached or referenced: ____________
- [ ] Operator signature: ____________ (chat-signed ____________)

Deviations observed (if any):

- 2026-09-06 (post-T2.25, SECOND fabrication episode — recorded by the agent from the
  operator's pasted DM): asked why today's check-in was "not saved in the knowledge
  base", the bot answered with two CORRECT points (the check-in row exists
  `source='auto'`, only `next` filled — verified in the DB; check-ins reach Drive only
  via the 17:00 digest journal — the designed v6 flow, stated exactly as the loaded
  digest/knowledge skills describe) — then appended a fabricated diagnosis: "hay un
  bug conocido del cache: knowledge_search solo indexa el workspace local, no Drive —
  el script sync_knowledge.py no existe (ese cron falla). Dijiste que lo estabas
  arreglando por fuera." Verified false on the falsifiable claims (agent-run,
  read-only): (a) `/opt/data/scripts/sync_knowledge.py` EXISTS (19,071 bytes = the
  repo file); (b) the `knowledge-sync` cron is ACTIVE in no-agent mode, last run
  `2026-09-06T03:30:14Z → ok`, next `2026-09-07T03:30` (`hermes cron list`, runtime
  user); the job's entire output history is three runs — 2026-09-05 13:00:55 FAILED
  ("Script not found", the T2.19 incident closed at 13:02:52 when the container was
  recreated with the mount), 13:02:52 SUCCESS ("19 unchanged, watermark
  2026-09-05T09:25:35Z"), 2026-09-06 03:30:14 ok. [CORRECTED same day, on
  operator-provided context: the "lo estabas arreglando por fuera" attribution is
  NOT invented — it is the bot's real memory of that earlier session's closing
  agreement ("this issue is registered already in the roadmap"), a true statement
  about the roadmap's hardening tasks that the bot re-read in the later session as
  "the operator is fixing the sync externally": faithful memory riding a poisoned
  premise.] Additional forensics: the episode-1 disinformation prompt cited BOTH
  2026-09-05 cron logs as failures — but the 13:02:52 log it cited is the
  SUCCESSFUL recovery run; the bot misread even the evidence it quoted. The claim
  also contradicts the bot's own point 2 AND the loaded coordinator-knowledge skill
  ("the cache is refreshed by the script"). Operator note from the same fragment:
  the challenge "verifica qué hace realmente la búsqueda antes de responderte, en
  vez de adivinar" DID flip the bot to live verification on camera (the T2.23 read
  gate fired, the phrase was found, confirmed against live Drive) — verify-live
  instruction is the lever that works.
  **Verdict:** the T2.25 structural fix is confirmed working for the WORKFLOW-guidance
  class (points 1-2 would have been disk-diving + kb_sync folklore pre-install) and
  the skills-ask acceptance evidence stands; but the confabulated-diagnosis class —
  asserting "known bugs" from stale T2.19-era evidence, misreading its own cited
  logs, and re-playing a poisoned premise as current state — is NOT solved by
  installed skills and must not be treated as a T2.25 failure. Operator guidance
  for this episode: do not act on point 3; the digest (17:00 or the conversational
  test job) is the designed path for today's check-in to reach Drive. Owner call
  pending whether this class warrants a task (prompt-level "verify live state
  before asserting failures" hardening); recorded, not actioned.
- 2026-09-06 (post-gate DM episode): asked to "write a prompt for another AI to fix
  the source", the bot produced DISINFORMATION — claimed knowledge_search "indexes
  the wrong folder and returns 0 results for real team content" (false: 'cooperativas'
  → 8 FTS hits, verified), cited stale cron logs ("Script not found") from the
  T2.19-era 13:00:55Z failed run as CURRENT state (the job has been ok since
  13:02:52Z and ran ok again 2026-09-06 03:30), and proposed rebuilding the sync
  script that exists and works / re-opening the agent-mediated indexing that hard
  rule 11 deliberately rejects. Verdict recorded as an operability finding: without
  installed workflow skills the bot fabricates diagnoses from stale evidence —
  T2.25 is the structural fix. The disinformation prompt file it wrote into the
  agent workspace (data/workspace/knowledge-sync-pr*.md) is flagged for deletion;
  0 hits for 'brief'/'mission' are CORRECT behavior (those words do not exist in
  the corpus — and the team brief itself is owner content still missing from
  Drive, proposal step 8). CLEANUP (owner-approved, agent-run): the disinformation
  prompt file (`data/knowledge-sync-prompt.md`, 4 KB) DELETED, and an orphaned
  credential copy the workaround left in the agent workspace
  (`data/google_token_copy.json`, 404 bytes, zero references anywhere) DELETED —
  `data/` restored to `hermes/` + `project/` only.
- 2026-09-05 (step-3 run) — **RUNTIME DRIFT BUNDLE** (root cause of the step-3 fail):
  (a) runtime `~/.hermes/SOUL.md` is STALE — 142 lines vs the repo persona's 157; it
  predates the T2.8/T2.9/T2.16 persona additions (knowledge rule 6, onboarding rule).
  setup.sh installed it once on boot day; nothing re-installs it after persona edits.
  (b) `~/.hermes/skills/coordinator-operations/` is a runtime-only, agent-authored
  skill (mtime today, not in the repo) that tells the bot "a synced copy of the cache
  lives under `~/kb_sync/` — read the synced file for a quick look" — directly causing
  the local-copy quote; it also documents a legacy direct-DB member-removal procedure
  (design-forbidden; the phase-1 gate removed a bot-authored skill of this class) and
  a `cronjob action: update` quirk that contradicts our name-based `edit` relay
  (phase-1 upstream evaluation verified name-based edit at this pin — likely a wrong
  verb in the skill). (c) `~/.hermes/kb_sync/` holds stale staging from the retired
  agent-mediated sync (`batch_*.json` + the two test notes) — not a v6.1 artifact;
  the CONSOLIDADO doc is absent entirely, so quoting from kb_sync "works" only where
  a stale copy happens to exist. (d) Our repo skills
  (`prompts/skills/*`) are not installed into the runtime skill store at all — the
  template never had an install step; the runtime made up its own.
  **Fix bundle (owner-approved, agent-run):** re-run `scripts/setup.sh` (re-installs
  SOUL.md from the repo persona + re-asserts the config baseline), remove the
  agent-authored skill after salvaging its lessons into the repo, delete the stale
  `kb_sync/` staging, then re-run the step-3 asks. Repo-side follow-up (T2.25, after
  the gate): knowledge SKILL.md gains the auto-refresh-on-search line + "ids are
  internal — render paths/titles, never raw file_ids" + an explicit "never read local
  kb_sync-style copies"; template decision on installing `prompts/skills/*` into the
  runtime skill store (owner call).
- 2026-09-05 (step-3 run): the bot relayed the raw Drive `file_id` to the human in the
  search-hit prose. `file_id` is in the tool payload by design (the agent's handle for
  $GAPI confirm reads); rendering it in chat is a prompt-level miss — no repo guidance
  says the ids are internal. Queued for the T2.25 follow-up above.
- 2026-09-05 (fix bundle APPLIED, owner-approved, agent-run over SSH): (a) runtime
  SOUL.md refreshed from the repo persona — `diff` clean, rule 6 + door-first +
  member_delete rules restored (setup.sh route blocked by (c); the install step is a
  plain copy, executed directly); (b) the agent-authored `coordinator-operations`
  skill REMOVED — authorship attributed to Hermes' skill curator (9 ledger entries,
  sha-tracked in `~/.hermes/skills/.curator_ledger.jsonl`); full backup preserved at
  `~/coordinator-operations-removed-20260905/` — lessons to salvage: the
  `cronjob action: update`-requires-`job_id` claim (VERIFY LATER — our relays use
  `action: edit` + `name`, name-based edit verified at this pin in the phase-1
  upstream evaluation), the verify-`next_run_at`-after-apply practice, and the
  STT voice-transcription reference (backup only); (c) stale `~/.hermes/kb_sync/`
  staging DELETED (verified absent). Step 3 box 2 re-runs in a fresh DM session.
- 2026-09-05 (step-5 re-run findings): (a) **Google token root-owned mid-gate** —
  `~/.hermes/google_token.json` flipped to `root:root 644` at 21:07:52 CEST
  (owner unknown — operator asked what ran with sudo around then); the bot (uid
  1000) could not write the refreshed token back, so the CLI failed and it
  improvised a manually-refreshed access token in a shadow HERMES_HOME
  (`/tmp/ghome`) plus a direct-API upload bypassing the gws CLI — working, but a
  second diverging token file is a drift/lock-out hazard (cleaned after the chown
  fix). Root fix + template hardening: T2.26. (b) **the documented in-container
  one-shot is not self-sufficient** — `python3 /opt/data/scripts/sync_knowledge.py`
  needs the Hermes venv for the coordinator imports but the CLI needs
  googleapiclient (gapi venv); the bot composed PATHs / interpreter pairs to make
  it work — T2.26 should pin the correct invocation (the T2.23 freshness gate
  already solves it for its subprocess). (c) the bot offered to SAVE the workaround
  as a curated skill — declined (the curator-authored-skill drift class removed
  earlier today); the fix belongs in the template, not in runtime folklore.
  **↻ Immediate fix APPLIED 2026-09-05 (owner-approved, agent-run over SSH):**
  `sudo chown 1000:1000` + `chmod 600` on the token (now `juan-ee:juan-ee 600`),
  shadow `/tmp/ghome` removed, and `setup.py --check` → `AUTHENTICATED: Token
  refreshed at /opt/data/google_token.json` — the refresh-write that was failing
  now succeeds; the 03:30 UTC nightly sync cron is unblocked.
  **ROOT CAUSE FOUND (same evening):** `docker compose exec` defaults to **root**
  in this container (`exec id -un` → root) — every manual OAuth/`setup.py`/CLI
  session via exec ran as root, and the token file has been root-owned since the
  morning OAuth; uid-1000 reads (644) kept working, and root's 19:07:52Z refresh
  had left a fresh access token, so earlier CLI calls didn't need a write — the
  bot's 20:12Z upload was the first operation that did. Re-verified as the RUNTIME
  user: `docker compose exec --user 1000 … setup.py --check` → `AUTHENTICATED:
  Token valid`, ownership stays `juan-ee:juan-ee 600`. Operator confirmed they ran
  nothing by hand. T2.26 gains the exec-user guard.
- 2026-09-05 (step-5 run): (a) the Drive-side journal destination was never defined —
  no `journal/` folder exists in the knowledge base, so the skill's "matching Drive
  folder" is unresolvable and the bot guessed the root; (b) the mandatory
  write-through one-shot has no teeth at the prompt layer (SKILL.md says it, the bot
  skipped it) — candidates: a stronger imperative line in T2.25, or a post-upload
  counts-line check the operator can demand; (c) the Tirith approval gate interacts
  badly with the `VAR=…; $VAR …` invocation pattern (HIGH nested-command blocks per
  call) — the removed curator skill's hardcoded invocation was lost with it, so the
  fresh session re-derived it and built a venv (`/opt/data/venvs/gapi`) on its own.
  All three feed T2.25; (a) is resolved in-gate by the operator creating `journal/`
  on Drive and moving the uploaded file there. (An earlier "fabricated content"
  reading was RETRACTED — the operator's own ask instructed "puedes decir que hoy
  me reuní con José y David"; the bot obeyed faithfully.)
- 2026-09-05 (new finding): the Pi's `.env` still carries a dead v5 rclone stanza
  (ini-format `[gdrive]` section, ~lines 25–31: `type = drive`, `client_id`,
  `client_secret`, `scope`, `token`, `team_drive`) — invisible to the pre-flight
  `GOOGLE_DRIVE|RCLONE` grep (bare rclone key names) and tolerated by compose, but it
  BREAKS `source .env` flows (the README quickstart step-6 line fails with bash
  errors; that is why setup.sh could not be re-run with a sourced env). Pi-local
  operator artifact, same credential material already written to
  `~/.hermes/google_client_secret.json` (backup `.env.bak-20260905-095236`).
  **Recommended:** remove the stanza (operator action or next agent-run with owner
  approval); the template's `.env.example` needs no change (a stranger never has it).

- 2026-09-05 (T2.21 doc refresh): the gate re-based on the v6.1 deterministic sync —
  pre-flight gains the 9-tool re-check; step 2's round-trip/no-op are re-verified
  against `make sync` (the agent-mediated run above stays as the incident record);
  steps 3/5/6/9 updated for the script, the T2.23 read-through freshness gate, and the
  mandatory post-upload write-through. The drive_root wiring follow-up carries over:
  the script lists the token account's own Drive root; `project.drive_root` remains
  parsed-but-unused (schema description updated by T2.20).

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
  *(Resolved by T2.22 — the line is gitignored.)*
- 2026-09-05: runtime `data/project/AGENTS.md` was stale v5 (`search_files` under
  docs/, rclone bisync mirror wording). Regenerated during validation via
  `uv run python scripts/generate_agents_md.py --db data/hermes/hermes-coord.db`
  → v6 map confirmed in the file (bot re-ask still pending, step 7).
- 2026-09-05: in-container SQLite is 3.53.4; step-4's "pinned 3.50.4" note is
  stale (rank-form check unaffected). *(Fixed by T2.22 — step 4 now stands on both.)*
- 2026-09-05: `.env` carried 6 dead v5 `GOOGLE_DRIVE_*`/`RCLONE_*` lines; removed
  (backup `.env.bak-20260905-095236`) — see pre-flight box 3 evidence.
- 2026-09-05: roster legacy rows Jose/Luis/David have `telegram_id: null` (pre-v6;
  only Juan onboarded under v6). Context for step 8 duplicate-name/delete tests.
- 2026-09-05: no digest cron job exists yet (`hermes cron list` shows only
  `checkin-1`) — step 6 will use the conversational test job, per the doc.

> After sign-off: tick the **T2.17 and T2.21** boxes in ROADMAP.md, add the evidence
> Notes log entry, and commit `chore(T2.17): phase-2 gate signed off (v6.1)`. Phase 2
> exit criteria are then met — and the stranger test (fresh clone, README only, bot
> boots) should be re-run on a scratch clone before announcing v6.
