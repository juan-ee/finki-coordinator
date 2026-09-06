---
name: coordinator-schedules
description: Use when the owner asks to recalculate all check-in schedules — normally the morning after an EU DST transition.
category: productivity
---

# Skill: recalculate all check-in schedules

Use when the owner asks to "recalculate all check-in schedules" — normally the
morning after an EU daylight-saving transition (last Sunday of March, last Sunday of
October; transitions land at 01:00 UTC).

## Why this exists (do not skip this understanding)

Every check-in cron job fires on a fixed UTC clock (`M H * * *`). Wake times are local.
When a member's timezone changes its UTC offset, their job fires an hour off local time
until recalculated. Ecuador (America/Guayaquil) has no DST — its jobs never change;
only DST zones like Europe/Berlin need this twice a year.

**Why you must be asked interactively:** Hermes disables cron-management tools inside
cron sessions — a scheduled job cannot re-schedule other jobs. So there is no self-
healing script; this is a twice-a-year conversation with the owner. Put a phone reminder
on it; recomputing the next morning loses nobody any sleep.

## Procedure

1. **Get the roster:** `member_list` (all members — you need the inactive ones only to
   skip them; their jobs stay paused).
2. **For every ACTIVE member with a wake time,** call the tools so each schedule is
   recomputed inside plugin code — `member_update` with their stored wake (or an explicit
   no-op update) returns a fresh `cron_relay` (action `edit`, name `checkin-<id>`,
   schedule computed from the current instant via `scheduling.py`).
3. **Relay each `cron_relay` verbatim** to the cronjob tool, one per member. Do not
   batch, do not edit, do not skip. You never compute the UTC hour yourself — the relay
   already contains the right `M H * * *`.
4. **Report the outcome** in one line per member: name, old schedule → new schedule, job
   name. Note explicitly which members were skipped and why (inactive, or no wake set).

## Example (spring transition, Europe/Berlin)

> **Owner:** DST happened — recalc everyone.
>
> **Coordinator:** Recalculated 4 check-in schedules (2 affected):
> - Dora (Europe/Berlin): `checkin-4` 07:00 → 06:00 UTC ✅
> - Ana, Bruno, Caro (America/Guayaquil): unchanged, no DST — relays confirmed identical.
>
> So, what do we do next: nothing — jobs are live from the next cron tick.

## Cautions

- Run this in an INTERACTIVE session (a DM with the owner), never inside a cron run.
- After the transition, verify with one member that the fire time felt right — the
  digest's Flags will also surface any member whose check-in silently shifted.
- Only the owner initiates full-roster changes; a member changing their own wake time is
  the ordinary `member_update` flow, not this procedure.
