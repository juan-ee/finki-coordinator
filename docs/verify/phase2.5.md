# Phase 2.5 verification — the v7 knowledge restructure on the Pi (MANUAL GATE, v7)

> **Human-only.** A coding agent cannot run this gate: it needs the Raspberry Pi, the
> live Hermes container, the Cloudflare dashboard (tunnel + Access), a browser outside
> the Pi, the agent's google-workspace skill (`$GAPI`) against the real Drive, and a
> Telegram account. Work top to bottom; tick each `[ ]` as it passes; do not skip
> failures. Leave nothing half-recorded — this page is the phase's sign-off.
>
> **STOP CONDITION (read before anything else):** step 0 verifies `$GAPI` still works
> in-container at the pinned `HERMES_REF`. If it does **not**, the phase stops there:
> record the exact error + evidence, untick nothing, report to the owner. No
> workarounds exist by design (no rclone, no service accounts, no dependency installs).

**What Phase 2.5 shipped** (the thing being verified, T2.27–T2.34): the v7 fold of the
constitution and source of truth; the Drive→cache sync machinery DELETED (8-tool
plugin; the agent reads its own `docs/` files — file tools / ripgrep); the local KB
workspace (`data/project/docs/` is its own git repo, seeded + initialized by
`setup.sh` step 7/10); the MkDocs Material site (`make site-build` → `data/site/`,
caddy on 127.0.0.1:8080, `*/15` host crontab rebuild); the outbound-only Cloudflare
Tunnel exposing site + dashboard behind Cloudflare Access (Google SSO); the daily
03:00 UTC Drive backup (agent cron `knowledge-backup`, commit BEFORE upload, loud
failure); and the Drive inbox flow (`input/` → synthesize → `docs/` → `processed/`).

## 0. Pre-flight (absorbs T2.17's still-live checks)

- [ ] Pi repo pulled to the shipped phase-2.5 commit (`git log --oneline -1`; record:
      ____________). Working tree clean (`git status`).
- [ ] `.env` on the Pi carries the v7 additions and NO secret values in files:
      `grep -E 'HERMES_DASHBOARD|CLOUDFLARE_TUNNEL_TOKEN' .env` shows the four keys
      set (dashboard + basic-auth pair + tunnel token). Record the key NAMES only —
      never print values.
- [ ] **8-tool census (v7):** `git pull` + `docker compose up -d` first (bind mounts
      apply only at container creation), then ask the bot in a DM to list its
      coordinator tools → exactly **8** (`member_add`, `member_update`,
      `member_list`, `member_delete`, `checkin_submit`, `checkins_by_date`,
      `setting_get`, `setting_set`) and **no** `knowledge_search` / `knowledge_sync`.
      Record: ____________.
- [ ] **$GAPI credential (absorbed from T2.17):** in-container, as the RUNTIME user:
      `docker compose exec --user 1000 gateway python
      /opt/data/skills/productivity/google-workspace/scripts/setup.py --check`
      → `AUTHENTICATED`. Record: ____________.
- [ ] **Telegram door still closes (absorbed from T2.17):** from a NON-allowlisted
      Telegram account, DM the bot → no response / no session. Then confirm the
      door-first flow still works for an allowlisted account (one DM exchange; no
      roster change needed). Record: ____________.

## 1. The site, behind the tunnel (kb.<domain>)

- [ ] On the Pi: `make site-build` → `data/site/` regenerated (`ls -la data/site | head`;
      record the timestamp: ____________), files owned by your user (NOT root — the
      recipe maps the host uid).
- [ ] `docker compose ps`: caddy binds **127.0.0.1:8080 only**; gateway and
      cloudflared publish **no ports** ("no inbound ports" holds). Record
      `docker compose ps` output gist: ____________.
- [ ] On the Pi: `curl -s http://127.0.0.1:8080/ | head` renders the site index.
- [ ] From a device OUTSIDE the Pi: `https://kb.<domain>` renders the SAME site —
      after the Cloudflare login (Google SSO). Record the URL + date: ____________.
- [ ] Host crontab rebuild: `crontab -l | grep site-build` shows the marker + */15
      line; after 15 minutes the `kb.<domain>` content reflects a fresh edit to
      `data/project/docs/` (make a scratch edit, wait, verify, revert the edit and
      `git -C data/project/docs checkout -- .`). Record: ____________.

## 2. Cloudflare Access blocks non-team accounts

- [ ] From a NON-team Google account (create a throwaway if needed): open
      `https://kb.<domain>` → the Access deny page appears (no site content renders).
      **Screenshot attached** (path: ____________).
- [ ] Same for `https://board.<domain>` → deny page. **Screenshot attached**
      (path: ____________).
- [ ] A TEAM account reaches both (already shown in steps 1/3) — the policy is
      allow-team, deny-everyone-else.

## 3. The dashboard (/kanban) behind the gate

- [ ] From an outside device, `https://board.<domain>` → Cloudflare login →
      **basic-auth prompt** (the fail-closed second layer) → the Hermes dashboard
      loads with the bundled `/kanban` tab.
- [ ] Drag-drop works through the tunnel: move a kanban task column → verify the move
      persisted (reload the page; the task stays). Record the task name: ____________.

## 4. The 03:00 backup lands in Drive (invariants 1 + 3)

- [ ] `docker compose exec gateway hermes cron list` shows **knowledge-backup** at
      `0 3 * * *` (created by setup.sh step 10/10 or the printed in-container
      command). Record: ____________.
- [ ] Make a scratch edit under `data/project/docs/` (or wait for real activity), let
      the 03:00 UTC job run. In the Drive browser: `knowledge_base/` contains the
      docs tree, **file count matches** `find data/project/docs -type f | wc -l`
      (record both numbers: local ______ / Drive ______).
- [ ] The group chat received the one-line report (date + outcome + count). Record
      its gist: ____________.
- [ ] **Invariant 1 proof:** `git -C data/project/docs log --oneline -3` shows the
      daily commit dated BEFORE the Drive upload (the job's own transcript order:
      commit → upload → count). Record the commit SHA: ____________.
- [ ] **Invariant 3 drill (deliberate failure):** temporarily break the upload (e.g.
      revoke the Drive token's network by disabling wifi for the 03:00 run on a test
      day, or rename the Drive folder) → the job posts a LOUD group message naming
      the failed step AND a dated journal line lands under
      `data/project/journal/`. Restore, re-run, confirm recovery. Record: ____________.

## 5. Inbox end-to-end (invariant 2: synthesis vs copy-pipe)

- [ ] In the Drive browser: drop a small markdown doc with a distinctive invented
      phrase into `input/` (record the phrase: ____________).
- [ ] DM the bot: *"Process the Drive inbox."* → the agent lists `input/`, downloads
      to tmp, reads it, writes/merges a `.md` under `data/project/docs/`, and moves
      the original to `processed/`.
- [ ] Verify the transcript: the agent did **NOT** recite the document's content into
      chat as the "update" (one or two lines about what was filed, not the contents).
- [ ] Verify the artifact: the new/updated `.md` exists under `data/project/docs/`
      (record the path: ____________), `docs/index.md` updated if a file was added.
- [ ] Verify the move: the original sits in Drive `processed/` and is gone from
      `input/`.
- [ ] Negative check: the transcript shows no verbatim file-by-file copy-passing as
      "sync" (the v6.1 gate failure mode). If the agent hand-copied, FAIL this box
      and record the transcript excerpt.

## 6. Restore drill (the record survives the Pi)

- [ ] Simulate SD-card death: from a scratch clone (or a second directory), restore
      `docs/` from the last snapshot: `git clone` the backup repo if you push it
      somewhere, OR download `knowledge_base/` from Drive and re-init:
      `mkdir -p restore/docs && cd restore/docs && git init`, copy the downloaded
      tree in, commit. Record the method used: ____________.
- [ ] The restored tree matches the live record:
      `diff -r data/project/docs restore/docs` → only `.git` differences (record:
      ____________).
- [ ] The restored repo's git history carries the daily commits (the safety net
      travelled with the backup): `git -C restore/docs log --oneline | head`.
      Record: ____________.

## Sign-off

- [ ] All boxes above ticked (no unticked box may remain — step 0 is the STOP gate).
- [ ] Date / Pi OS version / Hermes ref: ____________ / ____________ / 29112bef
      (v2026.8.31, Hermes v0.21.0)
- [ ] Cloudflare tunnel + Access screenshots referenced above: ____________
- [ ] Operator signature: ____________ (chat-signed ____________)

Deviations observed (if any):

-

> After sign-off: tick the **T2.35** box in ROADMAP.md, add the evidence Notes log
> entry, and commit `chore(T2.35): phase-2.5 gate signed off (v7)`. Phase 2.5 exit
> criteria are then met — and the stranger test (fresh clone, README only, bot boots)
> should be re-run on a scratch clone before announcing v7.
