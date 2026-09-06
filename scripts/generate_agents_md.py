#!/usr/bin/env python3
"""Render the runtime data/project/AGENTS.md: roster table, folder structure, query map, policy.

Impure shell (AGENTS.md rule 3): this CLI owns all I/O — it reads members from the SQLite
database and writes the markdown file — and delegates the content to the pure
`render(members)`. Output is deterministic by construction (AGENTS.md rule 5): no
timestamps, no clock, nothing volatile, so a re-run over the same database is
byte-identical and simply overwrites the output file in place. telegram_id (or any
other secret) is never rendered.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from coordinator.db import connect
from coordinator.repositories import Member, MembersRepo

DEFAULT_OUT = Path("data") / "project" / "AGENTS.md"

_HEADER = """# AGENTS.md — runtime context for the coordinator bot

> **Scope:** this file governs the **coordinator bot** — the Hermes agent operating this
> project. It is **not** the template repository's engineering `AGENTS.md` (which governs
> the agents that *build* the template).
>
> **Generated** by `scripts/generate_agents_md.py` from the members database. Do not edit
> by hand: update the database (or the script) and re-run it. Output is deterministic —
> the same roster always renders byte-identical content.
"""

_ROSTER_INTRO = """
## Team roster

The members database is the live source of truth — `member_list` is the authoritative
query. This table is a deterministic snapshot of **all** members (active and inactive,
ordered by name). Wake times are each member's local "HH:MM".

"""

_ROSTER_EMPTY = """
## Team roster

*(No members are registered yet. Add them with `member_add`; the roster table appears
here on the next regeneration.)*
"""

_STRUCTURE = """
## Project layout: the Pi is the record; this folder is the agent workspace

The knowledge record is **local**: all team `.md` live under `docs/` in this very
folder — write them directly and read them back with file tools; plain search
(ripgrep) beats any cache. `docs/` is its own git repo (the safety net), rendered to
a static site, and backed up daily to Google Drive — Drive is **backup + inbox only**,
never live truth (`input/` = documents humans drop, `processed/` = already ingested,
`knowledge_base/` = the daily 03:00 UTC backup of `docs/`).

```text
data/project/docs/ (the record — git-versioned):
├── index.md                            ← curated map of docs/ — keep it current
├── product/brief.md                    ← mission — read it before any major ask
├── decisions/                          ← ADRs (numbered)
├── meetings/                           ← dated notes
└── howto/                              ← operational playbooks

data/project/ (agent workspace — local):
├── AGENTS.md                           ← GENERATED (roster + layout + query map)
├── README.md                           ← human onboarding; static
├── journal/                            ← daily digests (stay local; ride the backup)
├── inbox/                              ← scratch drop zone: anything unfiled
├── templates/                          ← brief.md · adr.md · meeting-notes.md
└── .archive/                           ← moved, never deleted
```
"""

_QUERY_MAP = """
## Query map

- **Mission & goals** → `docs/product/brief.md` — read it before any major ask.
- **Project questions** → search the `docs/` files directly (ripgrep beats a cache);
  the file you read IS the record.
- **Status & activity** → `journal/` by date, or the `checkins_by_date` tool.
- **Tasks** → the `kanban_*` tools — never files.
- **What's new** → Drive `input/` (documents humans dropped) + local `inbox/` scratch.
- **Who & availability** → `member_list` — never a file.
- **Templates** → live in `templates/` (local).
"""

_POLICY = """
## Editorial policy

- Knowledge-base changes are written **straight under `docs/`** — the git history is
  the safety net; there is no upload step. Update `docs/index.md` when you add a file.
- Synthesis vs copy-pipe: you may READ a source document and WRITE a synthesized
  `.md` — transformation is the job. Never recite a document into chat as the
  "knowledge base update"; never hand-copy files verbatim file-by-file as "sync".
- `journal/`, `inbox/` and `.archive/` are agent-writable; journals stay local and
  ride the daily Drive backup.
- Google Drive is never treated as live truth: the local `docs/` file always wins.
"""

_TABLE_HEADER = "| Name | Timezone | Wake | Role | Active |\n|---|---|---|---|---|\n"
_MISSING = "—"


def _roster_row(member: Member) -> str:
    """Render one roster table row (missing wake/role as a dash, active as yes/no)."""
    wake = member.wake or _MISSING
    role = member.role or _MISSING
    active = "yes" if member.active else "no"
    return f"| {member.name} | {member.timezone} | {wake} | {role} | {active} |\n"


def _roster_table(members: list[Member]) -> str:
    """Render the roster table (all members in DB order, which is ordered by name)."""
    return _TABLE_HEADER + "".join(_roster_row(member) for member in members)


def render(members: list[Member]) -> str:
    """Render the runtime AGENTS.md from fetched members (pure, deterministic, secret-free)."""
    if members:
        roster = _ROSTER_INTRO + _roster_table(members)
    else:
        roster = _ROSTER_EMPTY
    return _HEADER + roster + _STRUCTURE + _QUERY_MAP + _POLICY


def main(argv: list[str] | None = None) -> int:
    """Render the runtime AGENTS.md from the --db members table and write it to --out."""
    parser = argparse.ArgumentParser(
        description="Render the runtime AGENTS.md (roster, structure, query map) from members DB."
    )
    parser.add_argument("--db", required=True, metavar="PATH", help="SQLite members database")
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=str(DEFAULT_OUT),
        help=f"output markdown path (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)
    try:
        conn = connect(args.db)
        try:
            members = MembersRepo(conn).list()  # active=None: render all, ordered by name
        finally:
            conn.close()
        content = render(members)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content.encode("utf-8"))
    except (OSError, sqlite3.Error) as error:
        print(f"error: could not generate AGENTS.md: {error}", file=sys.stderr)
        return 1
    print(f"wrote {out_path} ({len(members)} members)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
