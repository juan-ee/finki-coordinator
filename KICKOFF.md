# KICKOFF.md — How to Run the Build

You need exactly **one prompt** to start, and the session runs the whole loop itself.
Pick a mode:

---

## Mode A — Autopilot (recommended)

Open **one new DSH session** in this repo and paste this:

```text
You are the orchestrator for this repository. Read AGENTS.md and ROADMAP.md now.

Execute the roadmap task-by-task, in order, starting from the first unchecked task.

Per non-DOC task:
1. Record the pre-task SHA (git rev-parse HEAD).
2. Dispatch ONE fresh implementer subagent. Its prompt must be self-contained and
   contain: the ROADMAP task block verbatim, the AGENTS.md hard rules, and these
   instructions: TDD red→green, uv toolchain via make, `make check` before every
   commit, commit style `feat(<TASK-ID>): …`. The test seams written in the task
   are pre-agreed — the implementer must not ask anyone to confirm them.
3. When the implementer reports back: run `make check` yourself. Red? Send the
   failure to the SAME subagent for a fix round.
4. Run the Review protocol from AGENTS.md: the code-review skill with fixed point =
   the pre-task SHA, spec = the task block, standards = AGENTS.md. Both axes are
   fresh sub-agents that see only the diff + spec + standards.
5. Hard violation → ONE fix round to a FRESH subagent carrying only the findings +
   the spec; re-review just the delta. Judgement call → record in the task's
   Notes line in ROADMAP.md.
6. Mark the checkbox [x], then print one line:
   `T0.7 ✅ | commits: abc1234 | tests: 11 passed | review: 1 finding, fixed`
   Then move to the next task.

DOC tasks: do them directly (no subagent, no review), mark [x], one-line report.

Rules of engagement:
- One implementer at a time — never parallel implementers on this repo.
- Never touch proposal.md or data/. Never commit .env or secrets.
- STOP and report to me when ANY of these happens:
  a) you reach a task tagged MANUAL-GATE (write its docs/verify/phaseN.md script,
     leave the box unchecked, tell me exactly what to do on the Pi),
  b) the same dependency blocks you twice in a row,
  c) a review finding is ambiguous and you cannot classify it hard-vs-judgement.
- If my terminal session ends: a successor session resumes from ROADMAP.md
  checkboxes + git log. State lives in the repo, not in your memory.

Start now with the first unchecked task. Work until the first STOP condition.
```

That's it. You'll come back to: per-task one-liners, review verdicts, and eventually
`STOP — MANUAL-GATE reached: run docs/verify/phaseN.md on the Pi`.

---

## Mode B — Manual (one paste per task, you drive)

**Step 1 — implementer.** Open a fresh session in the repo, paste:

```text
Read AGENTS.md. Implement task T0.7 from ROADMAP.md exactly as specified.
TDD: the tests named in the task are written FIRST and must fail before the
implementation exists. uv toolchain — every command through make. `make check`
green before every commit. Commit as `feat(T0.7): scheduling module`.
The test seams in the task block are pre-agreed. Report: commits, test list,
make check output, deviations (if any).
```

**Step 2 — reviewer.** In the SAME session (after the implementer finished), paste:

```text
Review since <the SHA you recorded before starting> — the spec is the T0.7 block
in ROADMAP.md, the standards are AGENTS.md.
```

(The `code-review` skill picks this up: two blind axes, findings side by side.)

**Step 3 — verdict.** You arbitrate: fix → paste findings back as a fix round;
clean → say `mark T0.7 done and update ROADMAP.md`.

---

## When is work FINISHED?

| Scope | It's finished when… | How you check |
|---|---|---|
| **One task** | checkbox `[x]` + review "no hard violations" + commit exists | the orchestrator's one-liner; `git log --oneline` |
| **One phase** | all boxes in the phase ticked + gate doc written | `grep -c "\- \[x\]" ROADMAP.md` vs task count |
| **The project** | all 31 ticked + 3 gate docs signed by you + final test: clone the repo to a fresh folder, follow only the README, and the bot boots | the "stranger test" |

Hard stop conditions (the session MUST stop and tell you): a MANUAL-GATE, a
twice-blocked dependency, an unclassifiable review finding.

---

## How the reviewer is triggered

**You never trigger it manually in Mode A** — the kick-off prompt makes the
orchestrator run the review after every task, per the Review protocol in AGENTS.md.
In Mode B, you trigger it with the Step-2 phrase above.

Under the hood (DSH mechanics): the orchestrator session dispatches fresh child
agents (the implementer, then the two review axes). DSH notifies the orchestrator
when each child finishes; it verifies `make check` itself, arbitrates, and continues.
Children are blind to each other by construction — a reviewer child literally cannot
see the implementer child's reasoning. That is what makes the review adversarial.
