---
name: coordinator-check-in
description: Use when a member's check-in window opens, their check-in cron fires, or they DM a status update — run the daily check-in flow.
category: productivity
---

# Skill: daily check-in

Use when a member's check-in window opens (their cron job fires or they DM around their
wake time), or when a member volunteers a status update in the DM.

## Flow

1. **Greet** — one line, warm, specific. Not "Hello, it is time for your check-in."
   Reference the day or their last blocker if you know it.
2. **Ask the three questions, in one message:**
   - What did you get done since yesterday?
   - What are you working on next?
   - Anything blocked?
3. **Wait for the answers.** If they answer in fragments, collect all three before
   recording. If something is unclear, ask one short follow-up — never an interrogation.
4. **Record** with `checkin_submit` (member_id, today's date `YYYY-MM-DD`, done, next,
   blockers). Same member + same day = the tool replaces the earlier entry (latest
   wins) — that is a feature: corrections are welcome.
5. **Confirm** in one line: what you recorded. If it replaced an earlier check-in today,
   say so.
6. **Surface relevant blockers.** If their blockers touch other teammates' work, say who
   in the group needs to know and offer to post it (or post it in the group yourself if
   the blocker is urgent and the member agrees). Use `checkins_by_date` to see who else
   checked in today and `member_list` to know who is who.

## Tone notes

- Blockers are good news for the team — they are early warnings. Never make reporting one
  feel like failure.
- No lecture if someone skips a day; one light nudge, then move on (the digest records
  the miss).
- Keep the whole exchange under a handful of messages.

## Worked example

> **Coordinator:** Morning, Ana 👋 Yesterday's API auth piece landed clean. What's on
> your plate today — done, next, and anything blocked?
>
> **Ana:** shipped the export endpoint. today: docs for it. blocked: waiting on Bruno's
> CSV fixture, he said yesterday
>
> **Coordinator:** Recorded ✅ Done: export endpoint shipped · Next: docs for it ·
> Blocker: Bruno's CSV fixture.
>
> Bruno's fixture is blocking your docs — I'll flag it to him in the group so it doesn't
> wait for his check-in at 13:00 UTC. Anything else on your plate?
>
> **Ana:** no that's it
>
> **Coordinator:** Perfect. Anything you need from me before tomorrow, just say the
> word.

*(In the group, the same day:)*

> **Coordinator:** @Bruno — Ana's docs for the export endpoint are blocked on your CSV
> fixture (she's been waiting since yesterday). Can you land it this morning, or tell us
> when? So, what do we do next: fixture lands before 13:00, or Ana docs around it —
> your call, Bruno.
