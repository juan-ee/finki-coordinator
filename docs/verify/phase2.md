# Phase 2 verification — member lifecycle & knowledge on the Pi (MANUAL GATE, v6)

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
- [ ] `.env` on the Pi contains Telegram + OpenRouter (+ `HERMES_UID/GID`) and **no**
      `GOOGLE_DRIVE_*`/`RCLONE_*` lines (`grep -E 'GOOGLE_DRIVE|RCLONE' .env` → empty).
- [ ] Container is up and carries the new plugin: ask the bot in a DM to list its
      coordinator tools → **10** tools incl. `member_delete`, `knowledge_sync`,
      `knowledge_search`.

## 1. $GAPI works in-container — THE FIRST ITEM (STOP if it fails)

- [ ] In a DM, ask the bot: *"List the files in our Drive root using your
      google-workspace skill."* → the agent invokes `$GAPI` and returns a real file
      listing from the team's Drive.

**If this box cannot be ticked:** STOP the entire phase here. Record on this page:
`hermes --version` output, the exact agent/tool error, and anything the agent logs.
Report to the owner. Do NOT attempt rclone, service accounts, manual OAuth, or any
other fallback — the v6 design has none on purpose (proposal §6.9).

## 2. knowledge_sync round-trip — DOWN (Drive → cache)

- [ ] **Test data first (Drive web UI):** in the Drive root create/upload 2 small
      markdown docs — `docs/notes/decisión.md` containing the word **decisión** and a
      sentence with a distinctive phrase (record it: ____________), and
      `docs/notes/howto-test.md` with a second distinctive phrase (record it:
      ____________).
- [ ] DM: *"Sync the knowledge base."* → the agent runs the plan call (watermark),
      lists/selects/downloads via $GAPI, then ingests. The tool result reports
      `synced: N` (N ≥ 2) and a watermark value. Record: ____________.
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

- [ ] With the bot idle, run in-container:

```sh
docker compose exec gateway python3 -c "import sqlite3; c = sqlite3.connect('/opt/data/workspace/hermes/hermes-coord.db'); c.execute("INSERT INTO knowledge_fts(knowledge_fts, rank) VALUES('integrity-check', -1)")"
```

      exits 0 (clean). **The rank form is required** — the plain
      `VALUES('integrity-check')` verifies only FTS-internal structure and does NOT
      compare against the external content table (pinned on SQLite 3.50.4, T2.13).

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

- *(none yet)*

> After sign-off: tick the T2.17 box in ROADMAP.md, add the evidence Notes log entry,
> and commit `chore(T2.17): phase-2 gate signed off`. Phase 2 exit criteria are then
> met — and the stranger test (fresh clone, README only, bot boots) should be re-run
> on a scratch clone before announcing v6.
