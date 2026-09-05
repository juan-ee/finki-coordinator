"""hermes_plugin contracts: registration bookkeeping, schema shape, dispatch wiring.

CI installs no Hermes, so this file doubles as the guarded-import proof: importing
coordinator.hermes_plugin and registering through FakeCtx must succeed with Hermes
absent. EXPECTED_FIELDS pins every TOOL_SPECS schema field-for-field against the
payload validation handlers.py enforces (T0.8), so schema drift fails here.
"""

import importlib
import json
import sys
from datetime import UTC, datetime

import pytest

from coordinator import handlers, hermes_plugin
from coordinator.hermes_plugin import TOOL_SPECS, dispatch, register_tools
from coordinator.repositories import (
    DEFAULTS,
    Checkin,
    CheckinsRepository,
    Member,
    MembersRepository,
    SettingsRepository,
)

AT_NOON = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)  # winter instant (T0.7 ground truth)
TOOL_NAMES = frozenset(
    {
        "member_add",
        "member_update",
        "member_list",
        "member_delete",
        "checkin_submit",
        "checkins_by_date",
        "setting_get",
        "setting_set",
        "knowledge_sync",
        "knowledge_search",
    }
)

# tool -> (exact properties with JSON types, exact required list) as handlers.py enforces
EXPECTED_FIELDS: dict[str, tuple[dict[str, str], list[str]]] = {
    "member_add": (
        {
            "name": "string",
            "timezone": "string",
            "wake": "string",
            "telegram_id": "integer",
            "role": "string",
        },
        ["name", "timezone", "wake", "telegram_id"],
    ),
    "member_update": (
        {
            "member_id": "integer",
            "wake": "string",
            "active": "integer",
            "timezone": "string",
            "role": "string",
            "name": "string",
            "telegram_id": "integer",
        },
        ["member_id"],
    ),
    "member_list": ({"active": "integer"}, []),
    "member_delete": ({"member_id": "integer"}, ["member_id"]),
    "knowledge_sync": ({"files": "array"}, []),
    "knowledge_search": ({"limit": "integer", "query": "string"}, ["query"]),
    "checkin_submit": (
        {
            "member_id": "integer",
            "date": "string",
            "done": "string",
            "next": "string",
            "blockers": "string",
            "source": "string",
        },
        ["member_id", "date"],
    ),
    "checkins_by_date": ({"date": "string"}, ["date"]),
    "setting_get": ({"key": "string"}, ["key"]),
    "setting_set": ({"key": "string", "value": "string"}, ["key", "value"]),
}


class FakeClock:
    """Deterministic Clock: returns a fixed aware UTC instant."""

    def __init__(self) -> None:
        self.instant = AT_NOON

    def now(self) -> datetime:
        """Return the fixed instant."""
        return self.instant


class FakeMembers:
    """Dict-backed MembersRepository fake mirroring MembersRepo."""

    def __init__(self) -> None:
        self._rows: dict[int, Member] = {}
        self._next_id = 1

    def add(
        self,
        *,
        name: str,
        telegram_id: int | None = None,
        timezone: str = "UTC",
        wake: str | None = None,
        role: str | None = None,
        active: int = 1,
        created_at: str,
    ) -> Member:
        """Insert a member and return the stored row."""
        member = Member(
            id=self._next_id,
            name=name,
            telegram_id=telegram_id,
            timezone=timezone,
            wake=wake,
            role=role,
            active=active,
            created_at=created_at,
            updated_at=created_at,
        )
        self._next_id += 1
        self._rows[member.id] = member
        return member

    def update(self, member_id: int, **_columns: object) -> Member | None:
        """Return the row untouched: update paths are covered in test_handlers, not here."""
        return self._rows.get(member_id)

    def get(self, member_id: int) -> Member | None:
        """Return one member by id, including inactive rows."""
        return self._rows.get(member_id)

    def list(self, *, active: int | None = None) -> list[Member]:
        """Return members ordered by name, optionally filtered to the given active flag."""
        rows = list(self._rows.values())
        if active is not None:
            rows = [member for member in rows if member.active == active]
        return sorted(rows, key=lambda member: member.name)


class FakeCheckins:
    """Dict-backed CheckinsRepository fake (upsert, latest wins); unused by these paths."""

    def __init__(self) -> None:
        self._rows: dict[tuple[int, str], Checkin] = {}
        self._next_id = 1

    def submit(
        self,
        *,
        member_id: int,
        date: str,
        done: str | None = None,
        next: str | None = None,
        blockers: str | None = None,
        source: str = "auto",
        created_at: str,
    ) -> Checkin:
        """Upsert the member's check-in for date and return the stored row."""
        checkin = Checkin(
            id=self._next_id,
            member_id=member_id,
            date=date,
            done=done,
            next=next,
            blockers=blockers,
            source=source,
            created_at=created_at,
        )
        self._next_id += 1
        self._rows[(member_id, date)] = checkin
        return checkin

    def by_date(self, date: str) -> list[Checkin]:
        """Return the date's check-ins ordered by member id."""
        rows = [row for (_member_id, day), row in self._rows.items() if day == date]
        return sorted(rows, key=lambda row: row.member_id)


class FakeSettings:
    """Dict-backed SettingsRepository fake mirroring SettingsRepo (DEFAULTS fallback)."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str) -> str:
        """Return the stored value for key, falling back to DEFAULTS (KeyError when unknown)."""
        if key in self._values:
            return self._values[key]
        return DEFAULTS[key]

    def set(self, key: str, value: str) -> None:
        """Insert or overwrite the stored value for key."""
        self._values[key] = value


class FakeCtx:
    """HermesContext fake: records register_tool calls verbatim for exact assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        schema: dict[str, object],
        handler: object,
        toolset: str,
    ) -> None:
        """Record one registration verbatim."""
        self.calls.append(
            {
                "name": name,
                "description": description,
                "schema": schema,
                "handler": handler,
                "toolset": toolset,
            }
        )


def _wire() -> tuple[MembersRepository, CheckinsRepository, SettingsRepository, FakeClock]:
    """Build a fresh fake stack typed as the handler Protocols."""
    return FakeMembers(), FakeCheckins(), FakeSettings(), FakeClock()


# --- register_tools -------------------------------------------------------------------


def test_register_tools_records_exactly_ten_registrations() -> None:
    """register_tools() calls ctx.register_tool once per TOOL_SPECS entry, all coordinator."""
    ctx = FakeCtx()
    members, checkins, settings, clock = _wire()

    registered = register_tools(
        ctx, members=members, checkins=checkins, settings=settings, clock=clock, knowledge=None
    )

    names = [call["name"] for call in ctx.calls]
    assert len(ctx.calls) == 10
    assert set(names) == TOOL_NAMES
    assert len(set(names)) == len(names)  # exactly one registration per tool
    assert all(call["toolset"] == "coordinator" for call in ctx.calls)
    assert all(isinstance(call["description"], str) and call["description"] for call in ctx.calls)
    assert registered == names  # the return value mirrors the registration order


# --- TOOL_SPECS schemas ---------------------------------------------------------------


def test_every_schema_is_well_formed() -> None:
    """All ten schemas: type object, dict properties, list required, required inside properties."""
    for name, spec in TOOL_SPECS.items():
        schema = spec["schema"]
        assert schema["type"] == "object", name
        assert isinstance(schema["properties"], dict), name
        assert isinstance(schema["required"], list), name
        assert set(schema["required"]) <= set(schema["properties"]), name
        assert schema["additionalProperties"] is False, name


def test_tool_specs_cover_exactly_the_ten_tools() -> None:
    """TOOL_SPECS has exactly the ten tool names, each wired to its handlers.py function."""
    assert set(TOOL_SPECS) == TOOL_NAMES
    for name, handler in (
        ("member_add", handlers.member_add),
        ("member_update", handlers.member_update),
        ("member_list", handlers.member_list),
        ("checkin_submit", handlers.checkin_submit),
        ("checkins_by_date", handlers.checkins_by_date),
        ("setting_get", handlers.setting_get),
        ("setting_set", handlers.setting_set),
        ("member_delete", handlers.member_delete),
        ("knowledge_sync", handlers.knowledge_sync),
        ("knowledge_search", handlers.knowledge_search),
    ):
        assert TOOL_SPECS[name]["handler"] is handler, name


def test_schemas_mirror_handler_payload_fields_exactly() -> None:
    """Schema properties/required mirror each handler's accepted payload fields.

    This pins property names, their JSON types, and the required list, plus the
    nested files item shape for knowledge_sync — the field-level contract only. It
    does NOT prove full handler/schema equality: null-as-absent leniency and
    non-empty-string strictness are handler contract details that properties and
    required lists cannot express (see the knowledge_sync docstring).
    """
    for tool, (fields, required) in EXPECTED_FIELDS.items():
        schema = TOOL_SPECS[tool]["schema"]
        properties = schema["properties"]
        assert isinstance(properties, dict), tool
        assert sorted(properties) == sorted(fields), tool
        if tool == "knowledge_sync":
            assert properties["files"] == {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string"},
                        "path": {"type": "string"},
                        "title": {"type": "string"},
                        "modified_time": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_id", "path", "title", "modified_time"],
                    "additionalProperties": False,
                },
            }, tool
        else:
            assert properties == {key: {"type": value} for key, value in fields.items()}, tool
        assert sorted(schema["required"]) == sorted(required), tool


def test_member_add_description_pins_telegram_id_source_and_duplicate_rule() -> None:
    """The member_add description tells the model how to source telegram_id (T2.8).

    The required list alone cannot say WHERE the value comes from or what to do on a
    collision: the description — the single source register_tools() injects into the
    registered schema as schema["description"] (T1.8), the text the model actually
    sees — must name the telegram_id requirement, its source (the sender's session
    context), and the duplicate-active-name rejection with its remedy. Reverting the
    description to the pre-T2.8 text ("Add an active member (name, timezone, wake);
    returns the create relay...") fails every assertion below.
    """
    description = TOOL_SPECS["member_add"]["description"]

    assert "telegram_id" in description
    assert "required" in description.lower()
    assert "session context" in description
    assert "duplicate active" in description.lower()
    assert "rejected" in description.lower()
    assert "update the existing row" in description


# --- dispatch wiring ------------------------------------------------------------------


def test_registered_schemas_carry_the_model_facing_description() -> None:
    """Registered schemas embed ToolSpec.description as schema["description"].

    Upstream serves schema["description"] to the model; the register_tool(description=...)
    value is ToolEntry registry metadata only and is NOT copied into a schema lacking one
    (v0.21.0 plugin docs) — so the schema must carry it at registration time.
    """
    ctx = FakeCtx()
    members, checkins, settings, clock = _wire()
    register_tools(
        ctx, members=members, checkins=checkins, settings=settings, clock=clock, knowledge=None
    )

    assert len(ctx.calls) == 10
    for call in ctx.calls:
        schema = call["schema"]
        assert isinstance(schema, dict), call["name"]
        assert schema.get("description") == TOOL_SPECS[call["name"]]["description"], call["name"]


def test_dispatch_member_add_reaches_the_member_add_handler() -> None:
    """A member_add payload inserts a member — proof the add handler (not update) ran."""
    members, checkins, settings, clock = _wire()

    result = dispatch(
        "member_add",
        {"name": "Alice", "timezone": "America/Guayaquil", "wake": "08:00", "telegram_id": 111},
        members=members,
        checkins=checkins,
        settings=settings,
        clock=clock,
    )

    # member_update on an unknown id would return ok=False; a created row plus the exact
    # create relay pins the routing to member_add.
    assert result["ok"] is True
    assert result["cron_relay"] == {
        "tool": "cronjob",
        "args": {"action": "create", "name": "checkin-1", "schedule": "0 13 * * *"},
    }
    member = members.get(1)
    assert member is not None
    assert member.name == "Alice"
    assert member.wake == "08:00"


def test_dispatch_setting_get_reaches_the_setting_get_handler() -> None:
    """setting_get dispatch reads through the wired settings fake (DEFAULTS fallback)."""
    members, checkins, settings, clock = _wire()

    result = dispatch(
        "setting_get",
        {"key": "digest_chat"},
        members=members,
        checkins=checkins,
        settings=settings,
        clock=clock,
    )

    assert result["ok"] is True
    assert result["data"] == {"key": "digest_chat", "value": DEFAULTS["digest_chat"]}
    assert result["cron_relay"] is None


def test_dispatch_unknown_tool_raises_keyerror() -> None:
    """An unknown tool name is a KeyError naming the valid tools (decided contract)."""
    members, checkins, settings, clock = _wire()

    with pytest.raises(KeyError, match="unknown tool"):
        dispatch(
            "member_purge",
            {},
            members=members,
            checkins=checkins,
            settings=settings,
            clock=clock,
        )


def test_dispatch_knowledge_sync_without_wired_knowledge_repo_raises_keyerror() -> None:
    """Dispatching knowledge_sync with knowledge=None raises the wiring-bug KeyError.

    A knowledge tool whose repository was never wired (knowledge=None is legal for
    the eight repo-free tools) is a wiring bug, not a payload error: the guard
    refuses loudly instead of letting the handler dereference a None repository.
    """
    members, checkins, settings, clock = _wire()

    with pytest.raises(
        KeyError, match="tool 'knowledge_sync' requires a wired knowledge repository"
    ):
        dispatch(
            "knowledge_sync",
            {},
            members=members,
            checkins=checkins,
            settings=settings,
            clock=clock,
            knowledge=None,
        )


def test_registered_callable_dispatches_with_the_wired_deps() -> None:
    """A callable register_tools() handed ctx routes a payload into the right handler."""
    ctx = FakeCtx()
    members, checkins, settings, clock = _wire()
    register_tools(
        ctx, members=members, checkins=checkins, settings=settings, clock=clock, knowledge=None
    )

    call = next(c for c in ctx.calls if c["name"] == "member_add")
    raw = call["handler"](
        {"name": "Bob", "timezone": "Europe/Berlin", "wake": "08:00", "telegram_id": 222},
        task_id="t-1",  # host-injected dispatch kwargs must be tolerated
        session_id="s-1",
        user_task=None,
    )
    result = json.loads(raw)

    assert isinstance(result, dict)
    assert result["ok"] is True
    member = members.get(1)
    assert member is not None
    assert member.name == "Bob"  # the effect landed in the repo register_tools() wired in
    assert member.timezone == "Europe/Berlin"


def test_dispatch_returns_the_handler_result_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """dispatch() returns the handler's exact dict object: no copy, no wrap, no mutation."""
    sentinel: dict[str, object] = {
        "ok": True,
        "summary": "sentinel",
        "cron_relay": None,
        "data": {"x": 1},
    }

    def fake_handler(
        payload: dict[str, object],
        members: object,
        checkins: object,
        settings: object,
        clock: object,
    ) -> dict[str, object]:
        return sentinel

    monkeypatch.setitem(
        TOOL_SPECS,
        "member_list",
        {
            "description": "fake",
            "schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "handler": fake_handler,
            "toolset": "coordinator",
        },
    )
    members, checkins, settings, clock = _wire()

    result = dispatch(
        "member_list",
        {"anything": True},
        members=members,
        checkins=checkins,
        settings=settings,
        clock=clock,
    )

    assert result is sentinel


# --- guarded import -------------------------------------------------------------------


def test_module_imports_with_hermes_absent() -> None:
    """A fresh import succeeds in a Hermes-less env: the guarded-import proof itself."""
    module = importlib.import_module("coordinator.hermes_plugin")

    assert module is hermes_plugin
    assert "hermes" not in sys.modules  # the adapter pulled in no Hermes at import time
    assert len(TOOL_SPECS) == 10
