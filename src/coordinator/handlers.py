"""Tool handlers: payload validation + orchestration + cron-relay building (pure core).

Every handler is a plain function with the uniform signature
handler(payload, members, checkins, settings, clock) so hermes_plugin (T0.9) can
dispatch mechanically; each handler uses only the deps it needs. All return the AGENTS.md
result contract {"ok", "summary", "cron_relay", "data"}. Repos arrive via the Protocols
from repositories.py and time via the Clock Protocol: no sqlite, no Hermes, no OS here,
and schedule strings are computed exclusively by calling scheduling.py (rule 4).
Payload validation is manual + typed: unknown fields are rejected (this enforces the
self-service rule — member_add has no "active" key), required fields and value types are
checked, and a JSON null in an optional field is treated as absent. Failure results carry
the actionable one-line summary in "summary" and empty "data".
"""

import re
from datetime import date, datetime
from typing import Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from coordinator import scheduling
from coordinator.repositories import (
    DEFAULTS,
    CheckinsRepository,
    MembersRepository,
    SettingsRepository,
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DIGITS_RE = re.compile(r"\d+")
_CHAT_ID_RE = re.compile(r"-?\d+")

# Allowed payload keys per tool. member_add deliberately has NO "active" key: members can
# add themselves, but only member_update changes membership status (self-service rule).
_ADD_FIELDS = frozenset({"name", "timezone", "wake", "telegram_id", "role"})
_UPDATE_FIELDS = frozenset(
    {"member_id", "wake", "active", "timezone", "role", "name", "telegram_id"}
)
_LIST_FIELDS = frozenset({"active"})
_SUBMIT_FIELDS = frozenset({"member_id", "date", "done", "next", "blockers", "source"})
_DATE_FIELDS = frozenset({"date"})
_KEY_FIELDS = frozenset({"key"})
_SET_FIELDS = frozenset({"key", "value"})


class Clock(Protocol):
    """Time source injected into handlers (rule 5): handlers never call datetime.now()."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC instant."""
        ...


def _result(
    ok: bool, summary: str, cron_relay: dict[str, object] | None, data: dict[str, object]
) -> dict[str, object]:
    """Assemble the AGENTS.md tool-result contract dict."""
    return {"ok": ok, "summary": summary, "cron_relay": cron_relay, "data": data}


def _fail(summary: str) -> dict[str, object]:
    """Build the failure result: ok False, no relay, empty data."""
    return _result(False, summary, None, {})


def _cron_relay(args: dict[str, str]) -> dict[str, object]:
    """Wrap pre-computed cronjob args into the verbatim-executable relay envelope."""
    return {"tool": "cronjob", "args": args}


def _utc_hhmm(cron_expr: str) -> str:
    """Render a cron expression from scheduling.py as "HH:MM UTC" for the summary line."""
    minute, hour = cron_expr.split()[:2]
    return f"{int(hour):02d}:{int(minute):02d} UTC"


def _reject_unknown(payload: dict[str, object], allowed: frozenset[str]) -> str | None:
    """Return an error naming the first unknown payload field (enforces the self-service rule)."""
    unknown = sorted(set(payload) - allowed)
    if unknown:
        return f"unexpected field {unknown[0]!r}: allowed fields are {', '.join(sorted(allowed))}"
    return None


def _missing_required(payload: dict[str, object], required: set[str]) -> str | None:
    """Return an error naming the first missing required field."""
    missing = sorted(required - set(payload))
    if missing:
        return f"missing required field {missing[0]!r}"
    return None


def _require_str(payload: dict[str, object], key: str) -> str | None:
    """Return an error when a required field is absent, empty, or not a string."""
    if key not in payload:
        return f"missing required field {key!r}"
    value = payload[key]
    if not isinstance(value, str) or value == "":
        return f"field {key!r} must be a non-empty string"
    return None


def _optional_str(payload: dict[str, object], key: str) -> str | None:
    """Return an error when an optional field is present but empty or not a string (null = absent)."""
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        return f"field {key!r} must be a non-empty string"
    return None


def _require_int(payload: dict[str, object], key: str) -> str | None:
    """Return an error when a required field is absent or not an int (bools rejected)."""
    if key not in payload:
        return f"missing required field {key!r}"
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        return f"field {key!r} must be an integer"
    return None


def _optional_int(payload: dict[str, object], key: str) -> str | None:
    """Return an error when an optional field is present but not an int (bools rejected; null = absent)."""
    value = payload.get(key)
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return None
    return f"field {key!r} must be an integer"


def _value_type_error(payload: dict[str, object]) -> str | None:
    """Return an error when the "value" field is absent or not a string (emptiness is per-key)."""
    value = payload.get("value")
    if value is None:
        return "missing required field 'value'"
    if not isinstance(value, str):
        return "field 'value' must be a string"
    return None


def _timezone_error(name: str) -> str | None:
    """Return an error unless the IANA zone name resolves (name validation, not schedule math)."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        return f"unknown timezone {name!r}: not a resolvable IANA zone ({type(exc).__name__})"
    return None


def _date_error(value: str) -> str | None:
    """Return an error unless value is a strict, calendar-valid ISO date "YYYY-MM-DD"."""
    if _DATE_RE.fullmatch(value) is None:
        return f"field 'date' must be an ISO date 'YYYY-MM-DD' (e.g. '2026-01-15'); got {value!r}"
    try:
        date.fromisoformat(value)
    except ValueError:
        return f"field 'date' is not a real calendar date: {value!r} (expected 'YYYY-MM-DD')"
    return None


def _valid_keys() -> str:
    """Render the three known setting keys for error messages."""
    return ", ".join(sorted(DEFAULTS))


def member_add(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
) -> dict[str, object]:
    """Add an active member and return the create relay for its checkin-<id> cron job."""
    error = (
        _reject_unknown(payload, _ADD_FIELDS)
        or _missing_required(payload, {"name", "timezone", "wake"})
        or _require_str(payload, "name")
        or _require_str(payload, "timezone")
        or _require_str(payload, "wake")
        or _optional_str(payload, "role")
        or _optional_int(payload, "telegram_id")
    )
    if error is not None:
        return _fail(error)
    name = cast("str", payload["name"])
    timezone_name = cast("str", payload["timezone"])
    wake = cast("str", payload["wake"])
    role = cast("str | None", payload.get("role"))
    telegram_id = cast("int | None", payload.get("telegram_id"))

    try:
        scheduling.validate_wake(wake)
    except scheduling.SchedulingError as exc:
        return _fail(str(exc))
    tz_error = _timezone_error(timezone_name)
    if tz_error is not None:
        return _fail(tz_error)

    now = clock.now()
    member = members.add(
        name=name,
        telegram_id=telegram_id,
        timezone=timezone_name,
        wake=wake,
        role=role,
        active=1,
        created_at=now.isoformat(),
    )
    cron_expr = scheduling.wake_to_cron_expr(wake, timezone_name, now)
    relay = _cron_relay({"action": "create", "name": f"checkin-{member.id}", "schedule": cron_expr})
    data: dict[str, object] = {"member_id": member.id}
    summary = f"{name} added; check-in job checkin-{member.id} created for {_utc_hhmm(cron_expr)}"
    return _result(True, summary, relay, data)


def member_update(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
) -> dict[str, object]:
    """Update a member row; wake/timezone changes re-schedule, active=0 pauses the job."""
    error = (
        _reject_unknown(payload, _UPDATE_FIELDS)
        or _require_int(payload, "member_id")
        or _optional_str(payload, "wake")
        or _optional_int(payload, "active")
        or _optional_str(payload, "timezone")
        or _optional_str(payload, "role")
        or _optional_str(payload, "name")
        or _optional_int(payload, "telegram_id")
    )
    if error is not None:
        return _fail(error)
    member_id = cast("int", payload["member_id"])
    wake = cast("str | None", payload.get("wake"))
    active = cast("int | None", payload.get("active"))
    timezone_name = cast("str | None", payload.get("timezone"))
    role = cast("str | None", payload.get("role"))
    name = cast("str | None", payload.get("name"))
    telegram_id = cast("int | None", payload.get("telegram_id"))

    if active is not None and active not in (0, 1):
        return _fail("field 'active' must be 0 (deactivate) or 1 (reactivate)")
    if wake is not None:
        try:
            scheduling.validate_wake(wake)
        except scheduling.SchedulingError as exc:
            return _fail(str(exc))
    if timezone_name is not None:
        tz_error = _timezone_error(timezone_name)
        if tz_error is not None:
            return _fail(tz_error)
    member = members.get(member_id)
    if member is None:
        return _fail(f"unknown member {member_id}: use member_list for valid ids")

    now = clock.now()
    updated = members.update(
        member_id,
        updated_at=now.isoformat(),
        name=name,
        telegram_id=telegram_id,
        timezone=timezone_name,
        wake=wake,
        role=role,
        active=active,
    )
    # members.get() above just confirmed the id, so update() cannot return None here.
    assert updated is not None

    # Relay precedence (documented decision): (1) active=0 pause WINS over any wake or
    # timezone edit — a paused job must not also be rescheduled, though the row keeps the
    # new values. (2) Otherwise an actual wake or timezone CHANGE versus the stored row
    # rebuilds the schedule through scheduling.py only (rule 4); a member whose stored
    # wake is None has no job to re-schedule. (3) active=1 alone relays nothing (T0.8
    # spec: reactivation carries no relay — un-pausing a paused job is a noted gap).
    cron_relay: dict[str, object] | None = None
    if active == 0:
        cron_relay = _cron_relay({"action": "pause", "name": f"checkin-{member_id}"})
        summary = f"{updated.name} deactivated; check-in job checkin-{member_id} paused"
    else:
        new_wake = wake if wake is not None else member.wake
        new_tz = timezone_name if timezone_name is not None else member.timezone
        wake_changed = wake is not None and wake != member.wake
        tz_changed = timezone_name is not None and timezone_name != member.timezone
        if (wake_changed or tz_changed) and new_wake is not None:
            cron_expr = scheduling.wake_to_cron_expr(new_wake, new_tz, now)
            cron_relay = _cron_relay(
                {"action": "edit", "name": f"checkin-{member_id}", "schedule": cron_expr}
            )
            summary = (
                f"{updated.name} updated; check-in job checkin-{member_id}"
                f" rescheduled to {_utc_hhmm(cron_expr)}"
            )
        elif active == 1:
            summary = f"{updated.name} reactivated"
        else:
            summary = f"{updated.name} updated"
    data: dict[str, object] = {"member_id": member_id}
    return _result(True, summary, cron_relay, data)


def member_list(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
) -> dict[str, object]:
    """List members (default: the active roster); data never carries telegram_id (DM privacy)."""
    error = _reject_unknown(payload, _LIST_FIELDS) or _optional_int(payload, "active")
    if error is not None:
        return _fail(error)
    active = cast("int | None", payload.get("active"))
    if active is not None and active not in (0, 1):
        return _fail("field 'active' must be 0 or 1")
    # Filter default (documented decision): active only — the coordinator's daily surface
    # is the live roster; pass {"active": 0} for the inactive (e.g. offboarding review).
    wanted = 1 if active is None else active
    rows = members.list(active=wanted)
    members_data: list[dict[str, object]] = [
        {
            "id": row.id,
            "name": row.name,
            "timezone": row.timezone,
            "wake": row.wake,
            "role": row.role,
            "active": row.active,
        }
        for row in rows
    ]
    data: dict[str, object] = {"members": members_data}  # telegram_id deliberately omitted
    return _result(True, f"{len(rows)} member(s) listed (active={wanted})", None, data)


def checkin_submit(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
) -> dict[str, object]:
    """Upsert a member's check-in for one date (latest wins); strict ISO date required."""
    error = (
        _reject_unknown(payload, _SUBMIT_FIELDS)
        or _require_int(payload, "member_id")
        or _require_str(payload, "date")
        or _optional_str(payload, "done")
        or _optional_str(payload, "next")
        or _optional_str(payload, "blockers")
        or _optional_str(payload, "source")
    )
    if error is not None:
        return _fail(error)
    member_id = cast("int", payload["member_id"])
    day = cast("str", payload["date"])
    date_error = _date_error(day)
    if date_error is not None:
        return _fail(date_error)
    member = members.get(member_id)
    if member is None:
        return _fail(f"unknown member {member_id}: use member_list for valid ids")
    source = cast("str | None", payload.get("source"))
    checkins.submit(
        member_id=member_id,
        date=day,
        done=cast("str | None", payload.get("done")),
        next=cast("str | None", payload.get("next")),
        blockers=cast("str | None", payload.get("blockers")),
        source=source if source is not None else "auto",
        created_at=clock.now().isoformat(),
    )
    data: dict[str, object] = {"member_id": member_id, "date": day}
    return _result(True, f"Check-in recorded for {member.name} on {day}", None, data)


def checkins_by_date(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
) -> dict[str, object]:
    """Return a date's check-ins in member-id order; strict ISO date required."""
    error = _reject_unknown(payload, _DATE_FIELDS) or _require_str(payload, "date")
    if error is not None:
        return _fail(error)
    day = cast("str", payload["date"])
    date_error = _date_error(day)
    if date_error is not None:
        return _fail(date_error)
    rows = checkins.by_date(day)
    rows_data: list[dict[str, object]] = [
        {
            "member_id": row.member_id,
            "done": row.done,
            "next": row.next,
            "blockers": row.blockers,
            "source": row.source,
        }
        for row in rows
    ]
    data: dict[str, object] = {"date": day, "checkins": rows_data}
    return _result(True, f"{len(rows)} check-in(s) recorded on {day}", None, data)


def setting_get(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
) -> dict[str, object]:
    """Read one setting (repo falls back to DEFAULTS); unknown keys fail naming valid keys."""
    error = _reject_unknown(payload, _KEY_FIELDS) or _require_str(payload, "key")
    if error is not None:
        return _fail(error)
    key = cast("str", payload["key"])
    try:
        value = settings.get(key)
    except KeyError:
        return _fail(f"unknown setting {key!r}: valid keys are {_valid_keys()}")
    data: dict[str, object] = {"key": key, "value": value}
    return _result(True, f"{key} = {value!r}", None, data)


def setting_set(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
) -> dict[str, object]:
    """Persist one setting after per-key validation; no cron relay in T0.8 (digest job later)."""
    error = (
        _reject_unknown(payload, _SET_FIELDS)
        or _require_str(payload, "key")
        or _value_type_error(payload)
    )
    if error is not None:
        return _fail(error)
    key = cast("str", payload["key"])
    value = cast("str", payload["value"])
    if key not in DEFAULTS:
        return _fail(f"unknown setting {key!r}: valid keys are {_valid_keys()}")
    value_error = _setting_value_error(key, value)
    if value_error is not None:
        return _fail(value_error)
    settings.set(key, value)
    data: dict[str, object] = {"key": key, "value": value}
    return _result(True, f"{key} set to {value!r}", None, data)


def _setting_value_error(key: str, value: str) -> str | None:
    """Validate a setting value per key; digest_time reuses the strict wake validation."""
    if key == "digest_time":
        try:
            scheduling.validate_wake(value)
        except scheduling.SchedulingError as exc:
            return f"digest_time: {exc}"
        return None
    if key == "nudge_limit":
        if _DIGITS_RE.fullmatch(value) is None:
            return f"nudge_limit must be a non-negative integer (digits only); got {value!r}"
        return None
    if key == "digest_chat":
        # Minimal check (documented decision): "dm" or a Telegram chat-id string — an
        # optional leading "-" followed by digits (group ids are negative).
        if value != "dm" and _CHAT_ID_RE.fullmatch(value) is None:
            return (
                f"digest_chat must be 'dm' or a chat-id string (optional '-', digits);"
                f" got {value!r}"
            )
        return None
    return None
