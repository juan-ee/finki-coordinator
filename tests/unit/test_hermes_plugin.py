"""hermes_plugin contracts: registration bookkeeping, schema shape, dispatch wiring.

CI installs no Hermes, so this file doubles as the guarded-import proof: importing
coordinator.hermes_plugin and registering through FakeCtx must succeed with Hermes
absent. EXPECTED_FIELDS pins every TOOL_SPECS schema field-for-field against the
payload validation handlers.py enforces (T0.8), so schema drift fails here.
"""

import importlib
import sys
from datetime import UTC, datetime

import pytest

from coordinator import handlers, hermes_plugin
from coordinator.hermes_plugin import TOOL_SPECS, dispatch, register
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
        "checkin_submit",
        "checkins_by_date",
        "setting_get",
        "setting_set",
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
        ["name", "timezone", "wake"],
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
        status_days: str | None = None,
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
            status_days=status_days,
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


# --- register -------------------------------------------------------------------------


def test_register_records_exactly_seven_registrations() -> None:
    """register() calls ctx.register_tool once per TOOL_SPECS entry, all toolset=coordinator."""
    ctx = FakeCtx()
    members, checkins, settings, clock = _wire()

    registered = register(ctx, members=members, checkins=checkins, settings=settings, clock=clock)

    names = [call["name"] for call in ctx.calls]
    assert len(ctx.calls) == 7
    assert set(names) == TOOL_NAMES
    assert len(set(names)) == len(names)  # exactly one registration per tool
    assert all(call["toolset"] == "coordinator" for call in ctx.calls)
    assert all(isinstance(call["description"], str) and call["description"] for call in ctx.calls)
    assert registered == names  # the return value mirrors the registration order


# --- TOOL_SPECS schemas ---------------------------------------------------------------


def test_every_schema_is_well_formed() -> None:
    """All 7 schemas: type object, dict properties, list required, required inside properties."""
    for name, spec in TOOL_SPECS.items():
        schema = spec["schema"]
        assert schema["type"] == "object", name
        assert isinstance(schema["properties"], dict), name
        assert isinstance(schema["required"], list), name
        assert set(schema["required"]) <= set(schema["properties"]), name
        assert schema["additionalProperties"] is False, name


def test_tool_specs_cover_exactly_the_seven_tools() -> None:
    """TOOL_SPECS has exactly the 7 tool names, each wired to its handlers.py function."""
    assert set(TOOL_SPECS) == TOOL_NAMES
    for name, handler in (
        ("member_add", handlers.member_add),
        ("member_update", handlers.member_update),
        ("member_list", handlers.member_list),
        ("checkin_submit", handlers.checkin_submit),
        ("checkins_by_date", handlers.checkins_by_date),
        ("setting_get", handlers.setting_get),
        ("setting_set", handlers.setting_set),
    ):
        assert TOOL_SPECS[name]["handler"] is handler, name


def test_schemas_mirror_handler_payload_fields_exactly() -> None:
    """Schema properties/required match what each handler enforces, types included."""
    for tool, (fields, required) in EXPECTED_FIELDS.items():
        schema = TOOL_SPECS[tool]["schema"]
        expected_properties = {key: {"type": value} for key, value in fields.items()}
        assert schema["properties"] == expected_properties, tool
        assert sorted(schema["required"]) == sorted(required), tool


# --- dispatch wiring ------------------------------------------------------------------


def test_dispatch_member_add_reaches_the_member_add_handler() -> None:
    """A member_add payload inserts a member — proof the add handler (not update) ran."""
    members, checkins, settings, clock = _wire()

    result = dispatch(
        "member_add",
        {"name": "Alice", "timezone": "America/Guayaquil", "wake": "08:00"},
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
        {"key": "digest_time"},
        members=members,
        checkins=checkins,
        settings=settings,
        clock=clock,
    )

    assert result["ok"] is True
    assert result["data"] == {"key": "digest_time", "value": DEFAULTS["digest_time"]}
    assert result["cron_relay"] is None


def test_dispatch_unknown_tool_raises_keyerror() -> None:
    """An unknown tool name is a KeyError naming the valid tools (decided contract)."""
    members, checkins, settings, clock = _wire()

    with pytest.raises(KeyError, match="unknown tool"):
        dispatch(
            "member_delete",
            {},
            members=members,
            checkins=checkins,
            settings=settings,
            clock=clock,
        )


def test_registered_callable_dispatches_with_the_wired_deps() -> None:
    """The callable register() hands ctx routes a payload into the right handler."""
    ctx = FakeCtx()
    members, checkins, settings, clock = _wire()
    register(ctx, members=members, checkins=checkins, settings=settings, clock=clock)

    call = next(c for c in ctx.calls if c["name"] == "member_add")
    result = call["handler"]({"name": "Bob", "timezone": "Europe/Berlin", "wake": "08:00"})

    assert isinstance(result, dict)
    assert result["ok"] is True
    member = members.get(1)
    assert member is not None
    assert member.name == "Bob"  # the effect landed in the fake register() wired in
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
    assert len(TOOL_SPECS) == 7
