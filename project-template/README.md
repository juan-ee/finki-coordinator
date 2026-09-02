# Project workspace

This folder is the team's shared knowledge base. It lives on the Pi under
`data/project/` and is kept in sync with the team's Google Shared Drive
(two-way sync, roughly every 15–30 minutes and at container boot).

## Folder map

| Folder | What it is | Who writes it |
|---|---|---|
| `AGENTS.md` | Generated briefing for the coordinator bot (roster, query map) | **Generated** — never hand-edit |
| `docs/product/brief.md` | What we're thriving for: mission, goals, success criteria, constraints | Humans, once at first boot (from `templates/brief.md`) |
| `docs/decisions/` | Architecture Decision Records: `0001-kebab-case.md`, `0002-`…, numbered | Anyone; one ADR per decision |
| `docs/meetings/` | Meeting notes: `YYYY-MM-DD-<topic>.md` | Anyone, same day as the meeting |
| `docs/howto/` | Operational playbooks (dev, deploy, tooling) | Anyone |
| `docs/index.md` | Curated map of `docs/` — reviewed monthly | Anyone |
| `inbox/` | Drop zone for anything unfiled | Anyone; the bot triages it weekly into `docs/**` or `.archive/` |
| `templates/` | Fill-in templates: brief, ADR, meeting notes, proposals | Rarely changes |
| `journal/` | Daily digest files written by the bot | **Generated** — don't hand-edit |
| `assets/` | Images, diagrams, binaries | Anyone |
| `people/` | Optional per-member notes | Anyone |
| `.archive/` | Old material, moved not deleted | The bot's curator pass; humans too |

## Working with the coordinator bot

- The bot reads `docs/product/brief.md` **before any major ask** — keep it current.
- The bot drafts into `inbox/` and never writes `docs/` directly: humans keep
  editorial control, the bot does the legwork.
- Members manage their own roster row by asking the bot; roster admin is owner-only.
- Don't hand-edit `journal/` or `AGENTS.md` — both are generated (`AGENTS.md` is
  rebuilt from the roster).

## Conflict note

Two-way sync means simultaneous edits can conflict (rare by construction: humans
edit from Drive, the bot writes from the Pi, usually different files). If the sync
reports a conflict, **do not run `--resync` by hand** — follow
`docs/howto/` (bisync recovery runbook) or ask the operator.
