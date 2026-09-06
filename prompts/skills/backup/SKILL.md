---
name: coordinator-backup
description: Use when the 03:00 UTC backup cron fires or the owner asks to back up the knowledge base — commit docs/ locally first, upload to Drive knowledge_base/, verify the server-side file count, post one line.
category: productivity
---

# Skill: daily Drive backup (03:00 UTC)

The 03:00 UTC job backs the knowledge record up to Google Drive. The ORDER is
load-bearing — **commit BEFORE upload** (invariant 1: the git history is the local
safety net and must never depend on Drive being reachable) — and a silent failure is
a production incident (invariant 3: loud group post + journal record). The upload is
a `$GAPI` CLI file operation: file content never flows through your context
(AGENTS.md rule 11).

**Path note:** this job runs with `data/project` as the working directory, so the
docs repo is `docs/` from here (the same folder the template calls
`data/project/docs/` from the repo root).

## Flow

1. **Commit first** — always, before touching Drive:
   `git -C docs add -A && git -C docs commit -m "daily backup <YYYY-MM-DD>"`
   "nothing to commit" is success for this step (say so in one line). Never upload
   uncommitted work, and never skip the commit because the upload looks risky.
2. **Upload the record** via `$GAPI drive upload`: for every file under `docs/`,
   upload it to `knowledge_base/<same-relative-path>` on Drive (create the
   `knowledge_base/` folder if it is missing). Use the google-workspace skill's
   own `drive upload` reference for the exact flag syntax of the pinned CLI — the
   OPERATION is fixed here: one CLI upload call per file, preserving the relative
   path. Never read file contents into your context to "help" the upload, and never
   recite documents into chat.
3. **Verify server-side and count:** list the Drive `knowledge_base/` folder via
   `$GAPI` and count the files there. A local count is NOT evidence — the count you
   report must come from the Drive listing.
4. **Post one line to the group:** date + outcome + server-side file count, e.g.
   "Backup 2026-09-07 ok: 14 files in knowledge_base/". If nothing was committed
   (no changes), still verify and post — the count is the proof of record.

## On failure (any step)

- Commit fails for a reason other than "nothing to commit", the upload errors, or
  the server-side count cannot be verified:
  1. **Post a LOUD group message** naming the failed step and the actual error line
     — a silent failure is a production incident; the team must know the backup did
     not land.
  2. **Record it:** append one dated line to `journal/<YYYY-MM-DD>.md` (relative to
     this working directory) so the day's digest carries the failure.
  3. **Stop.** Do not retry in a loop — report and leave repeated failures to the
     owner.

## Rules

- Commit BEFORE upload, always (invariant 1).
- The upload is a file operation, never a context operation (rule 11).
- The reported count is verified server-side in `knowledge_base/`.
- One backup per day; every failure is loud AND recorded (invariant 3).
