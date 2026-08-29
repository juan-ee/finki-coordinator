# Skill: daily digest (17:00 job)

Use when the digest cron fires (17:00 in the configured anchor timezone — the job's
schedule was computed by the plugin; relay schedules verbatim, never recompute).

## Flow

1. **Collect check-ins:** `checkins_by_date` for today's date (`YYYY-MM-DD`, anchored to
   the team's day — use the date the job fired on).
2. **Collect the board:** `kanban_list` for tasks moved, completed, or newly blocked
   today.
3. **Count the inbox:** how many files sit in `inbox/` awaiting triage.
4. **Write the journal entry** `journal/YYYY-MM-DD.md` (see template below). The journal
   is agent-writable — write it directly, no triage step.
5. **Post the condensed summary** to the delivery chat (the chat configured for the
   digest). The summary is short; the journal carries the detail. Link the journal path.
6. **Run `scripts/sync.sh`** (on-demand beat — proposal §3): pushes the fresh journal
   entry toward the Drive mirror ahead of the next scheduled bisync.
7. **Flag misses:** members who did not check in today appear in Flags with a one-line
   note — the morning nudge is the follow-up, not a thread tonight.

## Journal template (`journal/YYYY-MM-DD.md`)

```markdown
# Day digest — YYYY-MM-DD

## Check-ins

| Member | Done | Next | Blockers |
|---|---|---|---|
| Ana | export endpoint shipped | docs for it | ⚠️ Bruno's CSV fixture |
| Bruno | CSV fixture landed | review Ana's docs | — |

✅ = checked in clean · ⚠️ = has a blocker (named inline)

## Board moves

- `export-endpoint` → review
- `csv-fixture` → done

## Inbox

3 files awaiting triage (weekly triage files them Friday).

## Flags

- Caro: no check-in today (nudged in the morning; no reply).
```

## Group summary template

> **{project_name} — day digest, YYYY-MM-DD**
> 3/4 checked in. Shipped: export endpoint, CSV fixture. Blockers: none open — Ana's
> docs wait on Bruno's review. Board: 2 moves (1 done). Inbox: 3 files. Flags: Caro
> missed today, nudged this morning.
> Full notes: `journal/YYYY-MM-DD.md`

## Tone notes

- Facts first, tone last. The digest is the team's shared memory — flat, complete,
  scannable.
- Never editorialize about a person's miss beyond the flag line; context lives in the
  DM or the morning thread.
- If `checkins_by_date` returns zero rows AND no member was expected, still write the
  journal entry (an honest empty day) — the record must be continuous.
- If the sync (`scripts/sync.sh`) fails, say so in one line at the end of the journal
  entry — drift must be visible, not silent.
