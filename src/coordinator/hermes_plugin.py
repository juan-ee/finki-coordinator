"""Thin Hermes adapter: TOOL_SPECS + register/dispatch + runtime wiring.

No business logic lives here (AGENTS.md rule 3): the adapter maps tool names -> JSON
schemas -> the handlers.py functions; handler result dicts pass through dispatch()
untouched and are serialized to JSON only at the host boundary (upstream accepts str
tool results only — see _bind). Concrete wiring (repos, clock) happens in
register_tools()/wire_runtime() and only there. Hermes
itself is NOT a dependency and is never imported: the host context arrives injected via
the HermesContext Protocol, so nothing at module import time can fail when Hermes is
absent — the structural form of the guarded import. If Hermes symbols are ever needed
here, import them lazily inside a function under try/except ImportError, never at module
top level.

Imports are relative (from .handlers, not coordinator.handlers) because upstream loads
this directory as the package `hermes_plugins.coordinator` with __path__ set to the
plugin dir — there is no top-level `coordinator` package on sys.path inside the Hermes
container, so absolute self-imports would raise ModuleNotFoundError at plugin load.

The entrypoint upstream discovery actually calls is coordinator.register (package
__init__, single positional ctx): it uses wire_runtime() to build the SQLite-backed
repo stack + SystemClock at the runtime layout, then delegates to register_tools().
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NotRequired, Protocol, TypedDict, cast

from . import db
from .config import DEFAULT_FRESHNESS_TTL_MINUTES
from .handlers import (
    Clock,
    FreshnessOutcome,
    KnowledgeFreshness,
    checkin_submit,
    checkins_by_date,
    knowledge_search,
    member_add,
    member_delete,
    member_list,
    member_update,
    setting_get,
    setting_set,
)
from .repositories import (
    CheckinsRepo,
    CheckinsRepository,
    KnowledgeRepo,
    KnowledgeRepository,
    MembersRepo,
    MembersRepository,
    SettingsRepo,
    SettingsRepository,
)

HandlerFn = Callable[
    [dict[str, object], MembersRepository, CheckinsRepository, SettingsRepository, Clock],
    dict[str, object],
]
"""The uniform handler signature (payload + wired deps -> result dict) most tools target."""

KnowledgeHandlerFn = Callable[
    [
        dict[str, object],
        MembersRepository,
        CheckinsRepository,
        SettingsRepository,
        Clock,
        KnowledgeRepository,
        KnowledgeFreshness | None,
    ],
    dict[str, object],
]
"""Signature for the knowledge tools: the uniform deps plus the wired knowledge cache
and freshness gate (the gate is None when the host wired none)."""

SpecHandler = HandlerFn | KnowledgeHandlerFn
"""A TOOL_SPECS handler: knowledge tools take the cache as an extra trailing dep."""


class ToolSpec(TypedDict):
    """One TOOL_SPECS entry: host-facing description, payload schema, handler, toolset.

    takes_knowledge (optional, default False) marks the knowledge tools: dispatch
    hands them the wired KnowledgeRepository as an extra trailing argument.
    """

    description: str
    schema: dict[str, object]
    handler: SpecHandler
    toolset: str
    takes_knowledge: NotRequired[bool]


class HermesContext(Protocol):
    """Minimal host seam register() needs from Hermes (recorded by FakeCtx in tests)."""

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        schema: dict[str, object],
        handler: Callable[..., str],
        toolset: str,
    ) -> None:
        """Record one tool registration with the host."""
        ...


TOOL_SPECS: Final[dict[str, ToolSpec]] = {
    "member_add": {
        "description": (
            "Add an active member (name, timezone, wake) with their telegram_id REQUIRED —"
            " take it from the sender's session context; the door allowlist is operator-run"
            " via scripts/allow.sh. Duplicate active names are rejected: update the existing"
            " row instead. Returns the create relay for its check-in cron job."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "timezone": {"type": "string"},
                "wake": {"type": "string"},
                "telegram_id": {"type": "integer"},
                "role": {"type": "string"},
            },
            "required": ["name", "timezone", "wake", "telegram_id"],
            "additionalProperties": False,
        },
        "handler": member_add,
        "toolset": "coordinator",
        "takes_knowledge": False,
    },
    "member_update": {
        "description": (
            "Update a member row (wake, active, timezone, role, name, telegram_id);"
            " schedule changes relay an edit or pause."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "member_id": {"type": "integer"},
                "wake": {"type": "string"},
                "active": {"type": "integer"},
                "timezone": {"type": "string"},
                "role": {"type": "string"},
                "name": {"type": "string"},
                "telegram_id": {"type": "integer"},
            },
            "required": ["member_id"],
            "additionalProperties": False,
        },
        "handler": member_update,
        "toolset": "coordinator",
        "takes_knowledge": False,
    },
    "member_list": {
        "description": "List members (default: the active roster); rows omit telegram_id.",
        "schema": {
            "type": "object",
            "properties": {"active": {"type": "integer"}},
            "required": [],
            "additionalProperties": False,
        },
        "handler": member_list,
        "toolset": "coordinator",
        "takes_knowledge": False,
    },
    "member_delete": {
        "description": (
            "Owner-only: permanently remove a member row and their check-ins, and relay"
            " removal of their check-in cron job. member_update(active=0) is the"
            " reversible alternative."
        ),
        "schema": {
            "type": "object",
            "properties": {"member_id": {"type": "integer"}},
            "required": ["member_id"],
            "additionalProperties": False,
        },
        "handler": member_delete,
        "toolset": "coordinator",
        "takes_knowledge": False,
    },
    "checkin_submit": {
        "description": "Record one member's check-in for an ISO date (latest submission wins).",
        "schema": {
            "type": "object",
            "properties": {
                "member_id": {"type": "integer"},
                "date": {"type": "string"},
                "done": {"type": "string"},
                "next": {"type": "string"},
                "blockers": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["member_id", "date"],
            "additionalProperties": False,
        },
        "handler": checkin_submit,
        "toolset": "coordinator",
        "takes_knowledge": False,
    },
    "checkins_by_date": {
        "description": "List the check-ins recorded on an ISO date, ordered by member id.",
        "schema": {
            "type": "object",
            "properties": {"date": {"type": "string"}},
            "required": ["date"],
            "additionalProperties": False,
        },
        "handler": checkins_by_date,
        "toolset": "coordinator",
        "takes_knowledge": False,
    },
    "setting_get": {
        "description": (
            "Read one setting (digest_chat, nudge_limit); unset keys fall back to defaults."
        ),
        "schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
        "handler": setting_get,
        "toolset": "coordinator",
        "takes_knowledge": False,
    },
    "setting_set": {
        "description": "Persist one setting after per-key validation.",
        "schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        "handler": setting_set,
        "toolset": "coordinator",
        "takes_knowledge": False,
    },
    "knowledge_search": {
        "description": (
            "Search the local knowledge cache (SQLite FTS5) for team documents; returns"
            " the top hits (file_id/path/title/heading). The cache is a finding aid -"
            " confirm against the LIVE Drive original (via $GAPI) before quoting."
            " limit defaults to 3 and is capped at 10."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": knowledge_search,
        "toolset": "coordinator",
        "takes_knowledge": True,
    },
}


def dispatch(
    name: str,
    payload: dict[str, object],
    *,
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
    knowledge: KnowledgeRepository | None = None,
    freshness: KnowledgeFreshness | None = None,
) -> dict[str, object]:
    """Route one payload to its handler; return the handler's result dict untouched."""
    spec = TOOL_SPECS.get(name)
    if spec is None:
        raise KeyError(f"unknown tool {name!r}: known tools are {', '.join(sorted(TOOL_SPECS))}")
    if spec.get("takes_knowledge", False):
        if knowledge is None:
            raise KeyError(f"tool {name!r} requires a wired knowledge repository")
        return cast(
            "KnowledgeHandlerFn",
            spec["handler"],
        )(payload, members, checkins, settings, clock, knowledge, freshness)
    return cast(
        "HandlerFn",
        spec["handler"],
    )(payload, members, checkins, settings, clock)


def _bind(
    name: str,
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
    knowledge: KnowledgeRepository | None,
    freshness: KnowledgeFreshness | None = None,
) -> Callable[..., str]:
    """Return the host-facing callable dispatching a payload to this tool's handler.

    Upstream calls entry.handler(args, **dispatch_kwargs) — currently task_id/session_id/
    user_task (model_tools.handle_function_call) — and accepts str (or multimodal) results
    only (registry._normalize_handler_result), so the closure tolerates **_host kwargs
    (ignored: handlers are stateless in the host context; the payload carries the tool
    args) and serializes the AGENTS.md result dict to JSON at the host boundary.
    """

    def tool(payload: dict[str, object], **_host: object) -> str:
        """Dispatch one payload with the deps wired at registration time; return JSON."""
        result = dispatch(
            name,
            payload,
            members=members,
            checkins=checkins,
            settings=settings,
            clock=clock,
            knowledge=knowledge,
            freshness=freshness,
        )
        return json.dumps(result, default=str)

    return tool


def register_tools(
    ctx: HermesContext,
    *,
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
    knowledge: KnowledgeRepository | None,
    freshness: KnowledgeFreshness | None = None,
) -> list[str]:
    """Register every TOOL_SPECS tool with the host ctx; return the names in spec order.

    ToolSpec.description stays the single source: it is injected into the registered
    schema as schema["description"] (the model-facing text upstream serves) while the
    description= kwarg remains registry metadata — both derive from the same field.
    """
    registered: list[str] = []
    for name, spec in TOOL_SPECS.items():
        ctx.register_tool(
            name=name,
            description=spec["description"],
            schema={**spec["schema"], "description": spec["description"]},
            handler=_bind(name, members, checkins, settings, clock, knowledge, freshness),
            toolset=spec["toolset"],
        )
        registered.append(name)
    return registered


class SystemClock:
    """Concrete Clock reading the host wall clock — the single time source in src/.

    Rule 5 keeps datetime.now() out of the pure core; this adapter class is the one
    sanctioned production instantiation point of the injected Clock (runtime wiring only).
    """

    def now(self) -> datetime:
        """Return the current timezone-aware UTC instant."""
        return datetime.now(UTC)


FRESHNESS_SCRIPT_PATH = Path("/opt/data/scripts/sync_knowledge.py")
"""The compose-mounted sync script (T2.19) the in-container freshness gate runs."""

FRESHNESS_LAST_CHECK_KEY = "knowledge_last_freshness_check"
"""Settings-table row stamping the last freshness attempt (NOT an agent-facing knob:
it is deliberately absent from repositories.DEFAULTS so setting_get/set cannot touch
it)."""


class FreshnessGate:
    """Concrete KnowledgeFreshness: the T2.18 engine as a bounded subprocess.

    refresh() runs the deterministic sync script (the same one-shot the knowledge
    skill mandates after uploads) with a hard timeout and stdin detached, and stamps
    the attempt in the settings table regardless of outcome — the debounce must hold
    through a Drive outage. Failures degrade (the search then serves the cache);
    nothing here can hard-fail a read. Owns its own short-lived connection: the
    runtime store is WAL + busy_timeout, built for exactly this sharing.
    """

    def __init__(
        self,
        *,
        ttl_minutes: int,
        script_path: Path = FRESHNESS_SCRIPT_PATH,
        db_path: Path | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self._ttl = ttl_minutes
        self._script_path = script_path
        self._db_path = db_path if db_path is not None else default_db_path()
        self._timeout = timeout_seconds

    def ttl_minutes(self) -> int:
        """Return the configured freshness TTL in minutes."""
        return self._ttl

    def last_check(self) -> str | None:
        """Return the stored last-attempt stamp (None when never attempted)."""
        row = self._settings_row()
        return None if row is None else str(row)

    def refresh(self, now: str) -> FreshnessOutcome:
        """Run the sync script once; stamp the attempt; degrade on any failure."""
        try:
            result = subprocess.run(
                [sys.executable, str(self._script_path)],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._stamp(now)
            return FreshnessOutcome("degraded", f"freshness sync timed out after {self._timeout}s")
        except OSError as exc:
            self._stamp(now)
            return FreshnessOutcome("degraded", f"freshness sync could not start: {exc}")
        self._stamp(now)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            tail = detail[-1][:200] if detail else "no output"
            return FreshnessOutcome(
                "degraded", f"sync script failed (exit {result.returncode}): {tail}"
            )
        return FreshnessOutcome("refreshed", self._counts_line(result.stdout))

    def _counts_line(self, stdout: str) -> str:
        """Extract the script's ingested/watermark line (metadata only — rule 11)."""
        lines = [line.strip() for line in stdout.strip().splitlines() if line.strip()]
        for line in lines:
            if line.startswith("ingested"):
                return line
        return lines[-1] if lines else "no report output"

    def _settings_row(self) -> str | None:
        """Read the stamp row straight from the settings table (raw: not in DEFAULTS)."""
        conn = db.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (FRESHNESS_LAST_CHECK_KEY,)
            ).fetchone()
            return None if row is None else str(row[0])
        finally:
            conn.close()

    def _stamp(self, now: str) -> None:
        """Upsert the attempt stamp (SettingsRepo.set semantics, own connection)."""
        conn = db.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (FRESHNESS_LAST_CHECK_KEY, now),
            )
            conn.commit()
        finally:
            conn.close()


def default_db_path() -> Path:
    """Return the runtime coordinator DB path (<HERMES_HOME>/workspace/hermes/hermes-coord.db).

    docker-compose.yml mounts the host's ./data at $HERMES_HOME/workspace, so the store
    seeded by scripts/init_db.py at <repo>/data/hermes/hermes-coord.db (its documented
    --db location) appears in-container at <HERMES_HOME>/workspace/hermes/hermes-coord.db;
    HERMES_HOME is always set inside the Hermes container (upstream image default
    /opt/data), ~/.hermes is the upstream host-side fallback.
    """
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "workspace" / "hermes" / "hermes-coord.db"


def _freshness_ttl_from_config() -> int:
    """Read knowledge.freshness_ttl_minutes from the runtime config layout.

    docker-compose mounts ./config read-only at /opt/data/config (T2.23), so the
    operator's validated boot default is visible in-container. Any failure — file
    absent (host dev checkouts), optional deps unimportable, invalid YAML/schema —
    falls back to the documented default with a stderr note: the TTL must never take
    the toolset boot down.
    """
    home = os.environ.get("HERMES_HOME")
    if not home:
        return DEFAULT_FRESHNESS_TTL_MINUTES
    candidate = Path(home) / "config" / "config.yaml"
    if not candidate.is_file():
        return DEFAULT_FRESHNESS_TTL_MINUTES
    try:
        from .config import ConfigError, load_config
    except ImportError as exc:
        print(f"warning: freshness TTL falls back to default ({exc})", file=sys.stderr)
        return DEFAULT_FRESHNESS_TTL_MINUTES
    try:
        return load_config(candidate).knowledge.freshness_ttl_minutes
    except ConfigError as exc:
        print(f"warning: freshness TTL falls back to default ({exc})", file=sys.stderr)
        return DEFAULT_FRESHNESS_TTL_MINUTES


def wire_runtime() -> tuple[
    MembersRepo, CheckinsRepo, SettingsRepo, KnowledgeRepo, SystemClock, FreshnessGate
]:
    """Open the runtime SQLite store (migrating as needed) and return the wired stack.

    Migrations are idempotent (versioned), so a fresh store is brought up to the shipped
    schema at gateway boot; seeding stays an explicit operator step (scripts/init_db.py).
    The freshness gate shares the store (WAL + busy_timeout) and runs the sync script
    subprocess on due searches.
    """
    clock = SystemClock()
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    db.migrate(conn, clock.now().isoformat())
    return (
        MembersRepo(conn),
        CheckinsRepo(conn),
        SettingsRepo(conn),
        KnowledgeRepo(conn),
        clock,
        FreshnessGate(
            ttl_minutes=_freshness_ttl_from_config(),
            db_path=path,
        ),
    )
