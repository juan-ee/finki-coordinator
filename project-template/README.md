# Project workspace

This folder is the coordinator bot's workspace on the Pi (`data/project/`) — and the
home of the team's knowledge record: everything under `docs/` is THE record,
git-versioned (this workspace's `docs/` folder is its own git repo — the history is
the safety net). A static site is rendered from `docs/` and exposed through a
Cloudflare Tunnel. Google Drive is **backup + inbox only**: the daily 03:00 UTC job
pushes `docs/` to Drive `knowledge_base/`, humans drop documents into Drive
`input/`, and the bot moves them to `processed/` after ingesting them.

## Folder map

| Folder | What it is | Who writes it |
|---|---|---|
| `AGENTS.md` | Generated briefing for the coordinator bot (roster, query map) | **Generated** — never hand-edit |
| `docs/product/brief.md` | What we're thriving for: mission, goals, success criteria, constraints | Anyone; the bot reads it first |
| `docs/decisions/` | Architecture Decision Records: `0001-kebab-case.md`, `0002-`…, numbered | Anyone; one ADR per decision |
| `docs/meetings/` | Meeting notes: `YYYY-MM-DD-<topic>.md` | Anyone, same day as the meeting |
| `docs/howto/` | Operational playbooks (dev, deploy, tooling) | Anyone |
| `docs/index.md` | Curated map of `docs/` — keep it current | Anyone |
| `inbox/` | Scratch drop zone for anything unfiled | Anyone; the bot triages it into `docs/**` or `.archive/` |
| `templates/` | Fill-in templates: brief, ADR, meeting notes, proposals | Rarely changes |
| `journal/` | Daily digest files written by the bot (stay local; ride the backup) | **Generated** — don't hand-edit |
| `assets/` | Images, diagrams, binaries | Anyone |
| `people/` | Optional per-member notes | Anyone |
| `.archive/` | Old material, moved not deleted | The bot's curator pass; humans too |

## Working with the coordinator bot

- The bot reads `docs/product/brief.md` **before any major ask** — keep it current.
- The bot writes knowledge-base changes **directly under `docs/`** (synthesis over
  copy-pipe: it reads source documents and writes new `.md` — it never recites a
  document into chat as an "update", and never hand-copies files verbatim as "sync").
- Members manage their own roster row by asking the bot; roster admin is owner-only.
- Don't hand-edit `journal/` or `AGENTS.md` — both are generated (`AGENTS.md` is
  rebuilt from the roster).

## Safety net

`docs/` is a git repo: its history protects every edit locally, and the daily Drive
backup (`knowledge_base/`) is the off-site copy. When in doubt, ask the operator.
