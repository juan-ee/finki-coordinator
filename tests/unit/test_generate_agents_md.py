"""generate_agents_md contract: golden render, byte-identical reruns, no secrets in output.

Seam choice (T0.12 design contract, documented): the golden, telegram-privacy and
empty-roster tests call the pure ``render()`` directly with Member dataclasses fetched
from a fixture SQLite database (tmp_path, fixed created_at strings); the impure CLI
shell (--db read -> render -> write, rerun byte-identity, in-place overwrite, error
exit) runs end-to-end via subprocess. GOLDEN and GOLDEN_EMPTY below are independent
literals: the implementation must conform to them, never the other way round.
"""

import importlib.util
import pathlib
import subprocess
import sys

from coordinator.db import connect, migrate
from coordinator.repositories import Member, MembersRepo

SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "generate_agents_md.py"

FIXED_CREATED_AT = "2026-01-15T08:00:00+00:00"

# Four members, one inactive (Carla); Bob has no telegram_id; Dave has no wake/role.
FIXTURE_MEMBERS: list[dict[str, object]] = [
    {
        "name": "Alice",
        "telegram_id": 101,
        "timezone": "America/Guayaquil",
        "wake": "08:00",
        "role": "backend",
    },
    {
        "name": "Bob",
        "timezone": "Europe/Berlin",
        "wake": "08:30",
        "role": "ops",
    },
    {
        "name": "Carla",
        "telegram_id": 103,
        "timezone": "America/Guayaquil",
        "wake": "09:00",
        "role": "frontend",
        "active": 0,
    },
    {
        "name": "Dave",
        "telegram_id": 104,
        "timezone": "UTC",
    },
]

EXPECTED_NAMES = ("Alice", "Bob", "Carla", "Dave")
EXPECTED_TIMEZONES = ("America/Guayaquil", "Europe/Berlin", "UTC")
EXPECTED_TELEGRAM_IDS = ("101", "103", "104")
EXPECTED_QUERY_MAP_PHRASES = (
    "docs/product/brief.md",
    "read it before any major ask",
    "search_files",
    "product/decisions/meetings/howto",
    "journal/",
    "checkins_by_date",
    "kanban_*",
    "inbox/",
    "member_list",
    "templates/",
)

GOLDEN = """# AGENTS.md — runtime context for the coordinator bot

> **Scope:** this file governs the **coordinator bot** — the Hermes agent operating this
> project. It is **not** the template repository's engineering `AGENTS.md`, which governs
> the agents that *build* the template.
>
> **Generated** by `scripts/generate_agents_md.py` from the members database. Do not edit
> by hand: update the database (or the script) and re-run it. Output is deterministic —
> the same roster always renders byte-identical content.

## Team roster

The members database is the live source of truth — `member_list` is the authoritative
query. This table is a deterministic snapshot of **all** members (active and inactive,
ordered by name). Wake times are each member's local "HH:MM".

| Name | Timezone | Wake | Role | Active |
|---|---|---|---|---|
| Alice | America/Guayaquil | 08:00 | backend | yes |
| Bob | Europe/Berlin | 08:30 | ops | yes |
| Carla | America/Guayaquil | 09:00 | frontend | no |
| Dave | UTC | — | — | yes |

## Project folder structure

`data/project/` mirrors the team's Shared Drive via rclone bisync (`project/**`). The
gateway runs with `data/project` as its working directory, so this file is injected into
sessions automatically.

```text
data/project/
├── AGENTS.md                        ← GENERATED (roster + structure + query map)
├── README.md                        ← human onboarding; static
├── docs/                            ← long-lived knowledge
│   ├── index.md                     ← curated map of docs/ (reviewed monthly)
│   ├── product/                     ← specs, roadmap, user research
│   │   └── brief.md                 ← what we're thriving for — read it first
│   ├── decisions/                   ← ADRs: 0001-kebab-case.md, 0002-… (numbered)
│   ├── meetings/                    ← 2026-08-28-sync.md (template in templates/)
│   └── howto/                       ← operational playbooks (dev, deploy, tooling)
├── journal/                         ← GENERATED daily digests (from checkins table)
├── inbox/                           ← drop zone: anything unfiled (agent triages weekly)
├── templates/                       ← brief.md · adr.md · meeting-notes.md · proposal.md
├── assets/                          ← images, diagrams, binaries
├── people/                          ← optional per-member notes (never the roster)
└── .archive/                        ← moved, never deleted (agent's curator pass)
```

## Query map

- **Mission & goals** → `docs/product/brief.md` — read it before any major ask.
- **Project questions** → `search_files` under `docs/` (product/decisions/meetings/howto).
- **Status & activity** → `journal/` by date, or the `checkins_by_date` tool.
- **Tasks** → the `kanban_*` tools — never files.
- **What's new** → `inbox/` triage.
- **Who & availability** → `member_list` — never a file.
- **Templates** → live in `templates/`.

## Editorial policy

- File the drafts you author into `inbox/` — never write into `docs/` directly.
- Weekly triage moves inbox drafts into `docs/**` and posts a "what I filed" summary to
  the group.
- `journal/`, `inbox/` and `.archive/` are agent-writable; `docs/` placement always goes
  through triage.
"""

GOLDEN_EMPTY = """# AGENTS.md — runtime context for the coordinator bot

> **Scope:** this file governs the **coordinator bot** — the Hermes agent operating this
> project. It is **not** the template repository's engineering `AGENTS.md`, which governs
> the agents that *build* the template.
>
> **Generated** by `scripts/generate_agents_md.py` from the members database. Do not edit
> by hand: update the database (or the script) and re-run it. Output is deterministic —
> the same roster always renders byte-identical content.

## Team roster

*(No members are registered yet. Add them with `member_add`; the roster table appears
here on the next regeneration.)*

## Project folder structure

`data/project/` mirrors the team's Shared Drive via rclone bisync (`project/**`). The
gateway runs with `data/project` as its working directory, so this file is injected into
sessions automatically.

```text
data/project/
├── AGENTS.md                        ← GENERATED (roster + structure + query map)
├── README.md                        ← human onboarding; static
├── docs/                            ← long-lived knowledge
│   ├── index.md                     ← curated map of docs/ (reviewed monthly)
│   ├── product/                     ← specs, roadmap, user research
│   │   └── brief.md                 ← what we're thriving for — read it first
│   ├── decisions/                   ← ADRs: 0001-kebab-case.md, 0002-… (numbered)
│   ├── meetings/                    ← 2026-08-28-sync.md (template in templates/)
│   └── howto/                       ← operational playbooks (dev, deploy, tooling)
├── journal/                         ← GENERATED daily digests (from checkins table)
├── inbox/                           ← drop zone: anything unfiled (agent triages weekly)
├── templates/                       ← brief.md · adr.md · meeting-notes.md · proposal.md
├── assets/                          ← images, diagrams, binaries
├── people/                          ← optional per-member notes (never the roster)
└── .archive/                        ← moved, never deleted (agent's curator pass)
```

## Query map

- **Mission & goals** → `docs/product/brief.md` — read it before any major ask.
- **Project questions** → `search_files` under `docs/` (product/decisions/meetings/howto).
- **Status & activity** → `journal/` by date, or the `checkins_by_date` tool.
- **Tasks** → the `kanban_*` tools — never files.
- **What's new** → `inbox/` triage.
- **Who & availability** → `member_list` — never a file.
- **Templates** → live in `templates/`.

## Editorial policy

- File the drafts you author into `inbox/` — never write into `docs/` directly.
- Weekly triage moves inbox drafts into `docs/**` and posts a "what I filed" summary to
  the group.
- `journal/`, `inbox/` and `.archive/` are agent-writable; `docs/` placement always goes
  through triage.
"""


def _load_script_module():
    """Load scripts/generate_agents_md.py as a module (the pure render seam lives there)."""
    spec = importlib.util.spec_from_file_location("generate_agents_md", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None  # a real script file is loadable
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate_agents_md = _load_script_module()


def _make_fixture_db(db_path: pathlib.Path) -> None:
    """Create and seed the fixture database at db_path (four members, one inactive)."""
    conn = connect(db_path)
    try:
        migrate(conn, applied_at=FIXED_CREATED_AT)
        repo = MembersRepo(conn)
        for member_kwargs in FIXTURE_MEMBERS:
            repo.add(created_at=FIXED_CREATED_AT, **member_kwargs)
    finally:
        conn.close()


def _fetched_fixture_members(tmp_path: pathlib.Path) -> list[Member]:
    """Seed the fixture database in tmp_path and return all members fetched from it."""
    _make_fixture_db(tmp_path / "hermes-coord.db")
    conn = connect(tmp_path / "hermes-coord.db")
    try:
        return MembersRepo(conn).list()  # active=None: all members, ordered by name
    finally:
        conn.close()


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run scripts/generate_agents_md.py with the venv python that is running pytest."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_render_golden_file(tmp_path: pathlib.Path) -> None:
    """render() output equals the embedded golden and carries roster + query-map content."""
    members = _fetched_fixture_members(tmp_path)

    rendered = generate_agents_md.render(members)

    for name in EXPECTED_NAMES:
        assert name in rendered
    for timezone in EXPECTED_TIMEZONES:
        assert timezone in rendered
    for phrase in EXPECTED_QUERY_MAP_PHRASES:
        assert phrase in rendered
    assert rendered == GOLDEN


def test_render_omits_telegram_ids(tmp_path: pathlib.Path) -> None:
    """Fixture telegram_id values never reach the rendered file (it syncs to a Drive)."""
    members = _fetched_fixture_members(tmp_path)

    rendered = generate_agents_md.render(members)

    for telegram_id in EXPECTED_TELEGRAM_IDS:
        assert telegram_id not in rendered
    assert "telegram" not in rendered.lower()


def test_render_empty_roster_is_deterministic_placeholder() -> None:
    """An empty roster renders a fixed placeholder line instead of a table."""
    rendered = generate_agents_md.render([])

    assert "no members" in rendered.lower()
    assert "| Name |" not in rendered
    assert rendered == GOLDEN_EMPTY


def test_cli_writes_golden_output_and_rerun_is_byte_identical(tmp_path: pathlib.Path) -> None:
    """The CLI writes the golden from the fixture DB; a rerun rewrites the same bytes."""
    db_path = tmp_path / "hermes-coord.db"
    out_path = tmp_path / "out.md"
    _make_fixture_db(db_path)

    first = _run_cli("--db", str(db_path), "--out", str(out_path))
    first_bytes = out_path.read_bytes()

    assert first.returncode == 0, first.stderr
    assert first_bytes == GOLDEN.encode("utf-8")

    second = _run_cli("--db", str(db_path), "--out", str(out_path))
    second_bytes = out_path.read_bytes()

    assert second.returncode == 0, second.stderr
    assert second_bytes == first_bytes


def test_cli_overwrites_existing_output_in_place(tmp_path: pathlib.Path) -> None:
    """A pre-existing --out file is replaced wholesale, not appended to or merged."""
    db_path = tmp_path / "hermes-coord.db"
    out_path = tmp_path / "out.md"
    _make_fixture_db(db_path)
    out_path.write_text("STALE CONTENT THAT MUST DISAPPEAR\n", encoding="utf-8")

    result = _run_cli("--db", str(db_path), "--out", str(out_path))

    assert result.returncode == 0, result.stderr
    assert out_path.read_bytes() == GOLDEN.encode("utf-8")


def test_cli_unreadable_database_fails_cleanly(tmp_path: pathlib.Path) -> None:
    """A --db that cannot be opened exits 1 with an actionable stderr and writes nothing."""
    db_path = tmp_path / "missing-dir" / "hermes-coord.db"
    out_path = tmp_path / "out.md"

    result = _run_cli("--db", str(db_path), "--out", str(out_path))

    assert result.returncode == 1
    assert result.stderr.startswith("error:")
    assert not out_path.exists()
