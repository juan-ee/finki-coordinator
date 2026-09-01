# Phase 1 verification — core bot on the Pi (MANUAL GATE)

> **Human-only.** A coding agent cannot run this gate: it needs the Raspberry Pi 5, the
> real Telegram bot, and a live Hermes container. Work top to bottom; tick each
> `[ ]` as it passes; do not skip failures. Total time: ~60–90 min (the ARM64 build
> dominates and is one-time).

## Preconditions (all must hold before starting)

- [ ] Pi 5 (8 GB, 64-bit OS on SSD), Docker + Compose installed (`docker --version`,
      `docker compose version`) — *(deviation: OS on SD card, no SSD attached — see
      Sign-off; Docker 29.7.2 + Compose v5.5.0 present)*.
- [x] Repo cloned on the Pi at the intended commit: `git log --oneline -1` matches the
      release commit you mean to ship. *(66be366 — finki-coordinator rename + README)*
- [x] `.env` filled (from `.env.example`): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`
      (contains YOUR user id), `OPENROUTER_API_KEY` ($20 monthly credit limit set in the
      OpenRouter console), Google Drive OAuth trio, `RCLONE_REMOTE`, optional
      `RCLONE_ROOT_FOLDER_ID`. *(all real; Drive trio verified by shape AND live OAuth
      exchange — Google minted a 1h access token; RCLONE_ROOT_FOLDER_ID empty by design:
      consumer Drive account, no Workspace)*
- [x] `config/config.yaml` filled (from `config/config.example.yaml`). *(hermes-demo /
      Fink-Labs / America/Guayaquil — validates against the schema)*
- [x] `./scripts/setup.sh` ran **on the Pi** in real mode (not `--dry-run`) and exited 0:
      it validated the config, wrote `rclone.conf`, installed SOUL.md, and printed the
      Hermes config fallback if the CLI was absent. *(exit 0; all 5 steps green; fallback
      printed with template default — actual pins applied in-container beforehand:
      model/cron.model = z-ai/glm-5.3-flash, timezone UTC; idempotent re-run planned)*
- [x] `./scripts/init_db.py --db data/hermes/hermes-coord.db --seed config/members.seed.yaml`
      ran once and exited 0 (re-run prints the `seeded_at` skip note and exits 0).
      *(seed 2026-09-01T17:37:13Z; re-run skip note captured; 4 members)*

## 1. ARM64 build succeeds

```
cd ~/finki-coordinator && time docker compose build
```

- [x] Build completes without error. **Record duration:** 11 min 36 s (696 s — well
      under the 20–60 min expectation; one-time, layers cached now)
- [x] `docker images | grep hermes-agent` shows the built image. *(hermes-agent:latest,
      4.08 GB)*

## 2. Container boots and the gateway comes online

```
docker compose up -d && docker compose logs -f gateway
```

- [ ] Gateway logs show the Telegram long-poll connecting (no auth errors, no crash
      loop). Ctrl-C out of the log tail.
- [ ] `docker compose ps` shows the gateway `running` with `network_mode: host`.
- [ ] DM the bot from an allow-listed account: it replies coherently (persona loaded —
      operator tone, not a generic assistant).

## 3. Coordinator plugin tools are visible in a DM session

In the DM, ask the bot:

> List the coordinator tools you have available, then show me the team roster.

- [ ] The bot's toolset includes all 7: `member_add`, `member_update`, `member_list`,
      `checkin_submit`, `checkins_by_date`, `setting_get`, `setting_set`.
- [ ] `member_list` returns the 4 seeded members (Ana/Bruno/Caro/Dora) with their
      timezones and wake times.
- [ ] **Self-service boundary:** ask the bot to change ANOTHER member's wake time → it
      must decline and point to the roster admin. Then change YOUR OWN row's wake via
      conversation → `member_update` runs and the bot relays a `cron_relay` call
      **verbatim** (inspect it: `docker compose logs gateway | grep cronjob`).

## 4. Kanban board reachable

In the DM:

> Create a kanban task "phase-1 verification" and then list the board.

- [ ] `kanban_create` + `kanban_list` work (board is Hermes-built-in; the bot can drive it).
- [ ] `~/.hermes/kanban.db` exists on the Pi host (bind mount side: inside the container's
      `/opt/data`).

## 5. Timezone matrix — the critical test

Goal: prove per-member schedules fire at the right UTC instants. Use TWO FAKE members so
real teammates are not spammed:

In the DM (as the owner):

> member_add "Fake Quito", telegram_id 900000001, America/Guayaquil, wake 08:00
> member_add "Fake Berlin", telegram_id 900000002, Europe/Berlin, wake 08:00

- [ ] Both `member_add` calls return a `cron_relay` that the bot relays verbatim:
      `checkin-5` at `0 13 * * *` (Quito, UTC−5) and `checkin-6` at
      `0 7 * * *` or `0 6 * * *` (Berlin, CET/CEST — match the current season).
- [ ] Verify the jobs exist: `hermes cron list` (inside the container:
      `docker compose exec gateway hermes cron list`) shows both jobs with those UTC
      schedules and `model` pinned (see step 6).
- [ ] **Fire test:** `docker compose exec gateway hermes cron trigger checkin-5` — the
      fake Quito member receives their check-in greeting in the expected window (within
      minutes of the trigger, greeting wording per the check-in skill).
- [ ] Same for `checkin-6` (Berlin). Delivery arrives in YOUR DM (fake members share your
      allow-listed account) — content correct, timing immediate (trigger ≠ schedule).
- [ ] **Schedule sanity:** wait for (or reason through) the next real fire time of
      `checkin-5`: it must be 08:00 Quito local = 13:00 UTC, independent of Berlin's
      offset. Check `hermes cron list` next-run timestamps.

## 6. `cron.model` pin visible on jobs

```
docker compose exec gateway hermes config get cron.model
docker compose exec gateway hermes cron list --json   # or plain list, per CLI
```

- [ ] `cron.model` equals the model in `config/config.yaml` (default
      `nousresearch/hermes-4-70b`) — the pin setup.sh applied.
- [ ] The two check-in jobs show that model pinned (unpinned jobs fail closed when the
      global default changes — this pin is the drift guard).
- [ ] Ask the bot to change its own model: it must refuse or route to the owner
      (model choice is the money valve).

## 7. AGENTS.md injected (query map)

In the DM:

> What is your query map? Where do you look for mission, decisions, status, tasks?

- [ ] The bot answers with the query map (mission → `docs/product/brief.md` first;
      decisions → `docs/decisions/`; status → `journal/` + `checkins_by_date`; tasks →
      `kanban_*`; who → `member_list`). This proves the generated runtime `AGENTS.md` is
      in the session working directory and injected at session start.
- [ ] Regenerate check: `python scripts/generate_agents_md.py --db data/hermes/hermes-coord.db`
      then start a NEW DM session and re-ask — the roster table reflects the current DB
      (AGENTS.md reaches the next session, not the next tool call).

## 8. Failure handling (if any step above fails)

- Capture `docker compose logs gateway --tail 200` and the exact bot transcript.
- Do NOT fix by hand-editing schedules; the reconciler is a prompt — ask the bot to
  "compare members against cron jobs and fix mismatches" (proposal §1).
- Record the failure in this file under a **## Sign-off** deviation note, leave the
  relevant box unticked, and stop.

## 9. Cleanup of fake members

In the DM (owner):

> member_update Fake Quito active=0, then member_update Fake Berlin active=0

- [ ] Both deactivations return `cron_relay` **pause** calls relayed verbatim; jobs
      `checkin-5`/`checkin-6` show paused in `hermes cron list`.
- [ ] (Optional) Remove the fake members' rows via SQL if you want a pristine roster:
      `sqlite3 data/hermes/hermes-coord.db "DELETE FROM checkins WHERE member_id>4;
      DELETE FROM members WHERE id>4;"`

## Sign-off

- [ ] All boxes above ticked (no unticked box may remain).
- [ ] Build duration recorded: ____________
- [ ] Date / Pi OS version / Hermes commit (HERMES_REF): ____________________________
- [ ] Operator signature: ____________________________

Deviations observed (if any):

- 2026-09-01: gate run PAUSED at step 3 — coordinator plugin not discovered by upstream
  (missing plugin.yaml manifest; root cause + fix plan documented in ROADMAP Notes log).
  Steps 2-9 boxes intentionally unticked. Pi OS on SD card (precondition 1) recorded above.
