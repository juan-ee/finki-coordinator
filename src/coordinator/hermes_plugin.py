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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, TypedDict

from . import db
from .handlers import (
    Clock,
    checkin_submit,
    checkins_by_date,
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
    MembersRepo,
    MembersRepository,
    SettingsRepo,
    SettingsRepository,
)

HandlerFn = Callable[
    [dict[str, object], MembersRepository, CheckinsRepository, SettingsRepository, Clock],
    dict[str, object],
]
"""The uniform handler signature (payload + wired deps -> result dict) every tool targets."""


class ToolSpec(TypedDict):
    """One TOOL_SPECS entry: host-facing description, payload schema, handler, toolset."""

    description: str
    schema: dict[str, object]
    handler: HandlerFn
    toolset: str


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
) -> dict[str, object]:
    """Route one payload to its handler; return the handler's result dict untouched."""
    spec = TOOL_SPECS.get(name)
    if spec is None:
        raise KeyError(f"unknown tool {name!r}: known tools are {', '.join(sorted(TOOL_SPECS))}")
    return spec["handler"](payload, members, checkins, settings, clock)


def _bind(
    name: str,
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
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
            name, payload, members=members, checkins=checkins, settings=settings, clock=clock
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
            handler=_bind(name, members, checkins, settings, clock),
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


def wire_runtime() -> tuple[MembersRepo, CheckinsRepo, SettingsRepo, SystemClock]:
    """Open the runtime SQLite store (migrating as needed) and return the wired stack.

    Migrations are idempotent (versioned), so a fresh store is brought up to the shipped
    schema at gateway boot; seeding stays an explicit operator step (scripts/init_db.py).
    """
    clock = SystemClock()
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    db.migrate(conn, clock.now().isoformat())
    return MembersRepo(conn), CheckinsRepo(conn), SettingsRepo(conn), clock
