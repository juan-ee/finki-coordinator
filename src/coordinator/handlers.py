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
from datetime import UTC, date, datetime
from typing import Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import scheduling
from .knowledge import Chunk, chunk_markdown
from .repositories import (
    DEFAULTS,
    CheckinsRepository,
    KnowledgeRepository,
    KnowledgeSearchError,
    MembersRepository,
    SettingsRepository,
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DIGITS_RE = re.compile(r"[0-9]+")  # ASCII only: Unicode Nd digits must not pass
_CHAT_ID_RE = re.compile(r"-?[0-9]+")

# Allowed payload keys per tool. member_add deliberately has NO "active" key: members can
# add themselves, but only member_update changes membership status (self-service rule).
_ADD_FIELDS = frozenset({"name", "timezone", "wake", "telegram_id", "role"})

# D1 (door-first onboarding): the add is COMPLETE or it fails. The agent takes the
# sender's Telegram ID from session context; the door itself is operator-run
# (scripts/allow.sh) and the bot never edits its own authorization.
_TELEGRAM_REQUIRED_MSG = (
    "telegram_id is required: take the sender's Telegram ID from session context "
    "(the door allowlist is operator-run via scripts/allow.sh)"
)
_UPDATE_FIELDS = frozenset(
    {"member_id", "wake", "active", "timezone", "role", "name", "telegram_id"}
)
_LIST_FIELDS = frozenset({"active"})
_DELETE_FIELDS = frozenset({"member_id"})
_SUBMIT_FIELDS = frozenset({"member_id", "date", "done", "next", "blockers", "source"})
_DATE_FIELDS = frozenset({"date"})
_KEY_FIELDS = frozenset({"key"})
_SET_FIELDS = frozenset({"key", "value"})
_SYNC_FIELDS = frozenset({"files"})
_SEARCH_FIELDS = frozenset({"query", "limit"})

# One knowledge_sync ingest entry: four required strings + optional text content
# (absent/None = non-text file, "" = empty text file: one title/path-only row either
# way, audit second-pass rule; only non-empty text content is chunked).
_FILE_REQUIRED_FIELDS = ("file_id", "path", "title", "modified_time")


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
    """Render the two known setting keys for error messages."""
    return ", ".join(sorted(DEFAULTS))


# SQLite INTEGER bind range: telegram_ids outside it are rejected before any store call.
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _telegram_id_error(telegram_id: int) -> str | None:
    """Return an error when a telegram_id exceeds SQLite's signed 64-bit integer range."""
    if _INT64_MIN <= telegram_id <= _INT64_MAX:
        return None
    return (
        f"telegram_id {telegram_id} is out of range: must fit a signed 64-bit integer "
        f"({_INT64_MIN} to {_INT64_MAX})"
    )


def _telegram_id_conflict(
    members: MembersRepository, telegram_id: int, exclude_id: int | None
) -> str | None:
    """Return an error when another member (excluding exclude_id) already holds the telegram_id."""
    # Pure-core pre-check for the store's UNIQUE(telegram_id) backstop: handlers cannot catch
    # sqlite3.IntegrityError without importing sqlite3, so they never see the constraint trip.
    for row in members.list(active=None):
        if row.id != exclude_id and row.telegram_id == telegram_id:
            return (
                f"telegram_id {telegram_id} already registered (member {row.id}, '{row.name}');"
                " update that member or choose a different id"
            )
    return None


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
    )
    if error is not None:
        return _fail(error)
    name = cast("str", payload["name"])
    timezone_name = cast("str", payload["timezone"])
    wake = cast("str", payload["wake"])
    role = cast("str | None", payload.get("role"))

    try:
        scheduling.validate_wake(wake)
    except scheduling.SchedulingError as exc:
        return _fail(str(exc))
    tz_error = _timezone_error(timezone_name)
    if tz_error is not None:
        return _fail(tz_error)

    # D1: telegram_id is REQUIRED (a JSON null counts as absent). Checked after the
    # wake/timezone value validation so the more specific errors win first.
    telegram_id = payload.get("telegram_id")
    if telegram_id is None:
        return _fail(_TELEGRAM_REQUIRED_MSG)
    if isinstance(telegram_id, bool) or not isinstance(telegram_id, int):
        return _fail("field 'telegram_id' must be an integer")
    tg_error = _telegram_id_error(telegram_id)
    if tg_error is not None:
        return _fail(tg_error)
    tg_conflict = _telegram_id_conflict(members, telegram_id, exclude_id=None)
    if tg_conflict is not None:
        return _fail(tg_conflict)

    # D1: duplicate ACTIVE member names are rejected with an actionable summary —
    # completing the existing row via member_update is the correct path, never a
    # second row. Matching is case-insensitive on trimmed names; an INACTIVE row
    # does not block an add (reactivation via member_update is its own path).
    wanted = name.strip().casefold()
    for row in members.list(active=1):
        if row.name.strip().casefold() == wanted:
            return _fail(
                f"{row.name} already exists as member {row.id} (active): complete or "
                "update that row via member_update instead of adding a duplicate"
            )

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
    """Update a member row; wake/timezone changes re-schedule, a real deactivation pauses."""
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
    if telegram_id is not None:
        tg_error = _telegram_id_error(telegram_id)
        if tg_error is not None:
            return _fail(tg_error)
    member = members.get(member_id)
    if member is None:
        return _fail(f"unknown member {member_id}: use member_list for valid ids")
    if telegram_id is not None:
        tg_conflict = _telegram_id_conflict(members, telegram_id, exclude_id=member_id)
        if tg_conflict is not None:
            return _fail(tg_conflict)

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

    # Relay precedence (documented decision): (1) a real deactivation (payload active=0
    # on a stored-active row) WINS over any wake or timezone edit — the job is paused,
    # not rescheduled, though the row keeps the new values. (2) active=0 on a row that
    # is ALREADY inactive is a no-op: no deactivation occurred and no schedule changed,
    # so cron_relay is None; other payload fields (e.g. wake) are still persisted on the
    # row but relay nothing either. (3) Otherwise an actual wake or timezone CHANGE
    # versus the stored row rebuilds the schedule through scheduling.py only (rule 4); a
    # member whose stored wake is None has no job to re-schedule. (4) active=1 alone
    # relays nothing (T0.8 spec: reactivation carries no relay — un-pausing a paused job
    # is a noted gap).
    cron_relay: dict[str, object] | None = None
    if active == 0 and member.active == 1:
        cron_relay = _cron_relay({"action": "pause", "name": f"checkin-{member_id}"})
        summary = f"{updated.name} deactivated; check-in job checkin-{member_id} paused"
    elif active == 0:
        summary = (
            f"{updated.name} is already inactive; check-in job checkin-{member_id} remains paused"
        )
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


def member_delete(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
) -> dict[str, object]:
    """Hard-remove a member (owner-only) and relay removal of their check-in cron job.

    D1: the repo's delete cascades the member's check-ins in the same commit. The
    cron_relay carries job removal only when the row had a wake: the documented
    assumption is that onboarding created a checkin-<id> job for such rows — job
    creation is the agent's member_add relay, not transactional with the row, so it
    cannot be verified from plugin state; if it never ran, the remove targets a
    nonexistent job, the same assumption the pause/edit relays make. A wake-less row
    never had a job, and the contract keeps cron_relay None when no schedule changed.
    """
    error = _reject_unknown(payload, _DELETE_FIELDS) or _require_int(payload, "member_id")
    if error is not None:
        return _fail(error)
    member_id = cast("int", payload["member_id"])
    member = members.get(member_id)
    if member is None:
        return _fail(f"unknown member {member_id}: use member_list for valid ids")

    members.delete(member_id)
    if member.wake is not None:
        relay = _cron_relay({"action": "remove", "name": f"checkin-{member_id}"})
        summary = f"{member.name} removed; check-in job checkin-{member_id} removed"
    else:
        relay = None
        summary = f"{member.name} removed (no check-in job existed)"
    data: dict[str, object] = {"member_id": member_id}
    return _result(True, summary, relay, data)


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
    """Validate a setting value per key (digest_time is dropped, D4 — unknown keys fail)."""
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


def _canonical_modified_time(value: str) -> str:
    """Normalize an RFC3339 modified_time to canonical UTC 'YYYY-MM-DDTHH:MM:SSZ'.

    A trailing Z is read as +00:00, an explicit offset is converted to UTC, and a
    timestamp without an offset is treated as UTC (the repo is UTC-anchored, and
    naive astimezone() would smuggle in the host's locale). An unparseable value is
    returned unchanged - the documented fallback that keeps bad Drive metadata
    visible in the cache instead of silently rewriting it.
    """
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds")[:-6] + "Z"


def knowledge_sync(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
    knowledge: KnowledgeRepository,
) -> dict[str, object]:
    """Sync the knowledge cache with Drive (two-call, agent-mediated $GAPI; D2).

    Call 1 - empty payload (plan): returns the derived watermark plus the $GAPI work
    order; the agent lists the Drive root, selects files with modifiedTime past the
    watermark, downloads those, and calls again with them. Call 2 - {files: [...]
    } (ingest): validates the WHOLE batch, then chunks + stores each file through
    the repo (absent content = non-text file, or empty text content: one
    title/path-only row either way; only non-empty text content is chunked). Each
    modified_time is normalized at ingest to canonical UTC
    'YYYY-MM-DDTHH:MM:SSZ' (a trailing Z is read as +00:00; an unparseable value
    is stored unchanged), so the watermark - MAX over the stored rows - ranks
    chronologically, never lexicographically across mixed offset forms; no partial
    ingest on a malformed batch.

    The handler is deliberately more lenient than the model-facing schema (the
    handlers-wide null-is-absent convention): a JSON null for files counts as
    absent — a plan call, like the empty payload — and content: null counts as
    absent (non-text). Same-batch duplicate file_ids are last-wins: only the last
    entry's rows stay stored for that file_id, while the result reports
    synced=len(entries) (entries, not unique files).
    """
    del members, checkins, settings  # uniform handler signature; the cache is external
    error = _reject_unknown(payload, _SYNC_FIELDS)
    if error is not None:
        return _fail(error)
    files = payload.get("files")
    if files is None:
        watermark = knowledge.watermark()
        hint = (
            "Use the google-workspace skill ($GAPI) to list the Drive root and download"
            " the text files whose modifiedTime is past the watermark"
            + (f" ({watermark})" if watermark is not None else " (empty cache: all files)")
            + "; then call knowledge_sync again passing files=[{file_id, path, title,"
            " modified_time, content}] (omit content for non-text files)."
        )
        data: dict[str, object] = {"watermark": watermark, "files_hint": hint}
        summary = f"knowledge sync plan ready (watermark: {watermark or 'empty cache'})"
        return _result(True, summary, None, data)
    if not isinstance(files, list):
        return _fail(
            "field 'files' must be a list of {file_id, path, title, modified_time, content} entries"
        )

    # Validate-then-apply: one malformed entry fails the whole batch (no partial ingest).
    validated: list[tuple[str, str, str, str, str | None]] = []
    for index, entry in enumerate(files):
        prefix = f"files[{index}]: "
        if not isinstance(entry, dict):
            return _fail(prefix + "each entry must be an object")
        unknown = sorted(set(entry) - {"file_id", "path", "title", "modified_time", "content"})
        if unknown:
            return _fail(prefix + f"unexpected field {unknown[0]!r}")
        for field in _FILE_REQUIRED_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str) or value == "":
                return _fail(prefix + f"{field!r} is required and must be a non-empty string")
        content = entry.get("content")
        if content is not None and not isinstance(content, str):
            return _fail(prefix + "field 'content' must be a string when present")
        validated.append(
            (
                cast("str", entry["file_id"]),
                cast("str", entry["path"]),
                cast("str", entry["title"]),
                cast("str", entry["modified_time"]),
                content,
            )
        )

    fetched_at = clock.now().isoformat()
    for file_id, path, title, modified_time, content in validated:
        # absent / empty / whitespace-only content all store the title/path-only row
        # (B5 + red-team residual: every ingested FILE leaves a trace and advances
        # the watermark; only non-blank text is chunked).
        chunks = (
            chunk_markdown(content)
            if content and content.strip()
            else [Chunk(heading=None, body="")]
        )
        knowledge.replace_file(
            file_id=file_id,
            path=path,
            title=title,
            modified_time=_canonical_modified_time(modified_time),
            fetched_at=fetched_at,
            chunks=chunks,
        )
    watermark = knowledge.watermark()
    data = {"synced": len(validated), "watermark": watermark}
    summary = f"{len(validated)} file(s) synced into the knowledge cache"
    return _result(True, summary, None, data)


def knowledge_search(
    payload: dict[str, object],
    members: MembersRepository,
    checkins: CheckinsRepository,
    settings: SettingsRepository,
    clock: Clock,
    knowledge: KnowledgeRepository,
) -> dict[str, object]:
    """Search the local knowledge cache (FTS5) - a finding aid, not the record (D2).

    Returns the top chunks (file_id/path/title/heading) ordered by bm25 with the
    title weighted 10:1 over the body; the agent confirms against the LIVE Drive
    original (via $GAPI) before quoting. limit defaults to 3 and is capped at 10.
    A query containing a C0 control character (NUL, tab, newline, ...) is rejected
    up front with ok:False: FTS5 MATCH silently truncates at an embedded NUL, so
    'alpha\\x00nomatchxyz' would match on plain 'alpha'. A malformed query surfaces
    as ok:False, never as a raw sqlite error.
    """
    del members, checkins, settings  # uniform handler signature; the cache is external
    error = _reject_unknown(payload, _SEARCH_FIELDS) or _require_str(payload, "query")
    if error is not None:
        return _fail(error)
    limit = payload.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        return _fail("field 'limit' must be an integer")
    if limit is not None and limit < 1:
        return _fail("field 'limit' must be >= 1")
    query = cast("str", payload["query"])
    effective = 3 if limit is None else min(limit, 10)
    # B2 guard: FTS5 MATCH silently truncates at an embedded NUL (C-string semantics),
    # so 'alpha\x00nomatchxyz' would return hits for plain 'alpha' - reject every C0
    # control character (< 0x20) before the query ever reaches the repo.
    control = next((ch for ch in query if ord(ch) < 0x20), None)
    if control is not None:
        return _fail(
            f"search failed: query contains control character U+{ord(control):04X}"
            " - plain words work best (the query is an FTS5 MATCH)"
        )
    try:
        hits = knowledge.search(query, effective)
    except KnowledgeSearchError as exc:
        return _fail(f"search failed: {exc} - plain words work best (the query is an FTS5 MATCH)")
    data: dict[str, object] = {
        "query": query,
        "results": [
            {
                "file_id": hit.file_id,
                "path": hit.path,
                "title": hit.title,
                "heading": hit.heading,
            }
            for hit in hits
        ],
    }
    return _result(True, f"{len(hits)} hit(s) for {query!r}", None, data)
