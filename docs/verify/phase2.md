# Phase 2 verification — knowledge base on the Pi (MANUAL GATE)

> **Human-only.** A coding agent cannot run this gate: it needs the Raspberry Pi, the
> live Hermes container, the real rclone remote, and a human editing the Drive in a
> browser. Work top to bottom; tick each `[ ]` as it passes; do not skip failures.
> Active wall-clock time: ~45 min; the cadence observation (step 2) spans ~35–40 min
> of passive waiting while the timer runs — do other work in between.

**What phase 2 shipped** (the thing being verified): `scripts/sync.sh` (guarded rclone
bisync wrapper, T2.1), `templates/` doc templates (T2.2), `project-template/` seed +
`setup.sh` step 7 first-boot copy that never overwrites (T2.3). Deliberately NOT in
this gate: backups (phase 4) and vector RAG (phase 5, forbidden until the team decides).

## 0. Pre-flight — carried-over DM verification (from the post-T1.9 abbreviated gate)

The T1.9 pin bump (HERMES_REF → v2026.8.31 = Hermes v0.21.0, commit 29112bef) was
executed on the Pi on 2026-09-02 — build 488 s, then headless verification only
(`hermes --version`, `hermes plugins list`, Telegram long-poll live, cron intact).
Its **DM half was left open with the owner** and is carried into this gate so it gets
verified on the new build. If you already did these after the bump, tick with a date
and pointer to the evidence instead of repeating them.

In a DM with the bot:

- [ ] **Persona voice:** the bot replies in the operator tone from `SOUL.md` (not a
      generic assistant; ends asks with a next-step proposal).
- [ ] **7 coordinator tools visible:** ask it to list its coordinator tools — all of
      `member_add, member_update, member_list, checkin_submit, checkins_by_date,
      setting_get, setting_set` are present and named (schema descriptions ride in the
      JSON schema since T1.8 — spot-check one tool's stated purpose matches).
- [ ] **Roster correct:** `member_list` returns the 4 founders
      (Juan/Jose/Luis/David) with timezones and wake times.
- [ ] **Kanban reachable:** create + list a kanban task (top-level `toolsets` flag
      still set: `docker compose exec gateway hermes config get toolsets`).

## Preconditions (all must hold before starting)

- [ ] Pi repo pulled to the intended commit: `git log --oneline -1` matches the
      shipped phase-2 commit (`7b2b8b1` or later).
- [ ] `.env` still holds the Drive trio + `RCLONE_REMOTE` (verified live in the
      phase-1 gate). **Decide the sync scope now:** `RCLONE_ROOT_FOLDER_ID` empty
      means the wrapper bisyncs the ENTIRE Drive root ⇄ `data/project/` (T2.1
      judgement call #1). On the consumer Drive account, create a dedicated folder
      (e.g. `Fink-Labs`), paste its folder id into `.env` as
      `RCLONE_ROOT_FOLDER_ID=…`. Every scope change after the first baseline requires
      re-running step 1's resync.
- [ ] `rclone` installed on the Pi **host** (`rclone version` — sync.sh runs
      host-side and reads `~/.config/rclone/rclone.conf` written by setup.sh step 3).
- [ ] `./scripts/setup.sh` re-run on the Pi in real mode, exit 0 — now 7 steps; the
      new step `7/7` seeds `data/project/` from `project-template/`.
- [ ] **Seed is first-boot-only:** edit `data/project/README.md` (add a line), re-run
      `./scripts/setup.sh`, confirm the edit survives and step 7 reports
      `(... already existed)` counts — the never-overwrite guarantee (T2.3).

## 1. Bisync baseline + cadence observed

```
cd ~/finki-coordinator
./scripts/sync.sh --resync --i-know-what-im-doing     # ONE-TIME baseline; read its output
tail -20 data/logs/sync.log
crontab -e   # add:  */20 * * * *  cd $HOME/finki-coordinator && ./scripts/sync.sh
```

- [ ] Baseline resync exits 0; `data/logs/sync.log` shows the
      `===== … bisync start (remote=… resync=1) =====` header and an end line with
      `rc=0`. Timestamps are UTC.
- [ ] Refusal guard still armed: `./scripts/sync.sh --resync` alone exits non-zero and
      names `--i-know-what-im-doing`.
- [ ] Cadence: at least TWO scheduled runs appear in `data/logs/sync.log` ~20 min
      apart (plain `bisync start` headers, no `[boot]` tag), both `rc=0`.
- [ ] Boot tag: run `./scripts/sync.sh --boot` once and confirm the
      `[boot] bisync start` marker in the log (this is what the container-boot
      invocation will look like).
- [ ] **Bidirectional proof:** (a) `touch data/project/inbox/pi-side.txt` → within one
      cycle the file appears on the Drive; (b) create `drive-side.txt` in the same
      Drive folder in the browser → within one cycle it appears in
      `data/project/inbox/` on the Pi. Clean both up afterwards.

## 2. Conflict drill — same file edited on both sides, recover, no data loss

```
cp -a data/project /tmp/project-backup-$(date -u +%Y%m%dT%H%M%SZ)   # safety net FIRST
```

- [ ] Safety snapshot taken (the command above; this is the "no data loss" oracle).
- [ ] Within one sync window: edit `data/project/docs/index.md` on the Pi (append
      `PI side edit`) AND edit the same file in the Drive web UI (append
      `DRIVE side edit`).
- [ ] The next scheduled run FAILS LOUDLY: exit non-zero, and `data/logs/sync.log`
      shows rclone naming the conflicting path (no silent side-picking).
- [ ] **Recovery (the never-blind-resync procedure** — T4.2 will formalize this as
      `docs/runbooks/bisync-recovery.md`; until then this box is the runbook):
      1. Pause the cadence: `crontab -e` → comment out the sync line.
      2. Read the conflict list from the last `sync.log` entry — note each path rclone
         named.
      3. Reconcile by hand: merge BOTH edits into one version of the file in
         `data/project/` (the Drive web UI's file history has the other side's text).
      4. Re-baseline: `./scripts/sync.sh --resync --i-know-what-im-doing` (guarded on
         purpose — resync takes the reconciled state as the new baseline; skipping
         step 3 is how bisync eats files).
      5. Re-enable the cron line; one normal run; `rc=0` in the log.
- [ ] **No data loss:** the recovered `docs/index.md` contains BOTH `PI side edit`
      and `DRIVE side edit`; diff the backup against the recovered tree
      (`diff -r /tmp/project-backup-* data/project`) and confirm every difference is
      explained. Delete the backup only after this check.

## 3. AGENTS.md regeneration

```
python scripts/generate_agents_md.py --db data/hermes/hermes-coord.db
```

- [ ] The command rewrites `data/project/AGENTS.md` (deterministic render: roster
      table, folder structure, query map).
- [ ] Change something in the roster (add a fake member via DM, or edit the DB) →
      regenerate → the rendered AGENTS.md reflects the change (then remove the fake
      member and regenerate again).
- [ ] The regenerated file reaches the Drive within one sync cycle (open it in the
      browser and see the change) — the bot's runtime briefing is mirrored, not just
      local.

## 4. Doc-extraction check — a PDF on the Drive is readable by the agent

- [ ] Upload a small PDF containing one distinctive nonsense phrase (write a text file
      with the phrase, export to PDF) into `inbox/` **on the Drive**.
- [ ] Within one sync cycle the PDF appears in `data/project/inbox/` on the Pi.
- [ ] DM the bot: *"Find the PDF in my inbox and tell me the exact phrase hidden in
      it."* → the agent reads the PDF (its file tools extract text) and returns the
      phrase. *(If the agent's toolset cannot extract PDF text, record that as a
      deviation with the model/tool names — do not tick.)*
- [ ] Cleanup: move the PDF to `.archive/` (or delete) and confirm the move propagates.

## Sign-off

- [ ] All boxes above ticked (no unticked box may remain — including the four step-0
      pre-flight boxes).
- [ ] Date / Pi OS version / Hermes ref: ____________ / ____________ / 29112bef
      (v2026.8.31, Hermes v0.21.0)
- [ ] Cadence interval chosen for production: ____________ (proposal §3: 15–30 min)
- [ ] Operator signature: ____________ (chat-signed ____________)

Deviations observed (if any):

- *(none yet)*

> After sign-off: tick the T2.4 box in ROADMAP.md, add the evidence Notes log entry,
> and commit `chore(T2.4): phase-2 gate signed off`. Phase 2 exit criteria are then met.
