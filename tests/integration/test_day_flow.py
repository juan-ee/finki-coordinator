"""Integration day-flow: seed -> check-ins (upsert) -> digest settings -> DST cron relays.

The full ROADMAP T1.5 day runs against ONLY real pieces (AGENTS.md rule 3): each test
reseeds a fresh tmp_path database by running scripts/init_db.py ONCE as a subprocess with
the committed config/members.seed.yaml, then drives real handlers over real repositories
on the real migrated connection. Nothing is mocked but the Clock (rule 5): a settable
FakeClock makes every created_at/updated_at stamp and every schedule resolution a fixed
aware UTC instant. The structure is one test per flow step (documented choice over one
end-to-end test: per-step tests keep pytest isolation and point failures at one arrow of
the ROADMAP sequence); together they are the full day.

Ground truth is pinned as literals (they must be able to disagree with scheduling.py):
America/Guayaquil stays UTC-5 all year (no DST) so a wake's cron string is identical at
both instants, Europe/Berlin is UTC+1 (CET) in winter and UTC+2 (CEST) in summer; the
2026 EU transitions (2026-03-29, 2026-10-25) leave 2026-01-15 and 2026-07-15 safely
inside standard and summer time. Cross-checked against scheduling.wake_to_cron_expr
before pinning.

Relay mechanics (documented choice): member_update wake edits in away-and-back pairs on
the seeded members -- a wake change relays the NEW wake's schedule, so the back-edit
restores the stored wake AND yields a relay for it at the current instant. member_add at
one instant was the alternative; update pairs keep the roster at the seeded 4 throughout.
"""

import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pytest

from coordinator.db import connect
from coordinator.handlers import (
    checkin_submit,
    checkins_by_date,
    member_update,
    setting_get,
    setting_set,
)
from coordinator.repositories import CheckinsRepo, MembersRepo, SettingsRepo

ROOT = Path(__file__).resolve().parents[2]
INIT_DB = ROOT / "scripts" / "init_db.py"
SEED_YAML = ROOT / "config" / "members.seed.yaml"

DAY = "2026-01-15"  # the fixed "today" every check-in lands on
MORNING = datetime(2026, 1, 15, 13, 0, tzinfo=UTC)  # flow instant (08:00 in Guayaquil)
WINTER = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)  # Berlin on CET (UTC+1)
SUMMER = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)  # Berlin on CEST (UTC+2)


class FakeClock:
    """Settable Clock: returns the instant a test last assigned (the only faked piece)."""

    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now(self) -> datetime:
        """Return the instant the test last set."""
        return self.instant


class Day(NamedTuple):
    """The composed stack for one day: real repos over the seeded database + FakeClock."""

    members: MembersRepo
    checkins: CheckinsRepo
    settings: SettingsRepo
    clock: FakeClock


@pytest.fixture()
def day(tmp_path: Path) -> Iterator[Day]:
    """Seed a fresh tmp database via the real init_db CLI and open the real repos on it."""
    db_path = tmp_path / "day.db"
    result = subprocess.run(
        [sys.executable, str(INIT_DB), "--db", str(db_path), "--seed", str(SEED_YAML)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    conn = connect(db_path)
    yield Day(MembersRepo(conn), CheckinsRepo(conn), SettingsRepo(conn), FakeClock(MORNING))
    conn.close()


def _member_ids(day: Day) -> dict[str, int]:
    """Map seeded member name to id via the real repo (list is name-ordered)."""
    return {member.name: member.id for member in day.members.list(active=1)}


def test_seed_lands_four_members_and_marker(day: Day) -> None:
    """init_db --seed with the committed YAML stores the 4 roster members and the marker."""
    members, _, settings, _ = day

    roster = {member.name: member for member in members.list(active=1)}

    assert set(roster) == {"Juan", "Jose", "Luis", "David"}
    assert [
        (roster[name].timezone, roster[name].wake) for name in ("Juan", "Jose", "Luis", "David")
    ] == [
        ("Europe/Berlin", "06:30"),
        ("America/Guayaquil", "06:00"),
        ("America/Guayaquil", "05:30"),
        ("America/Guayaquil", "11:00"),
    ]
    assert settings.get("seeded_at")  # marker exists and is a non-empty stamp


def test_checkins_upsert_to_four_rows_and_by_date(day: Day) -> None:
    """4 check-ins plus one corrected re-submit upsert to exactly 4 latest-wins rows."""
    members, checkins, settings, clock = day
    ids = _member_ids(day)
    first_pass = {
        "Juan": ("wrote the ADR draft", "circulate for review", "waiting on Pi"),
        "Jose": ("fixed the WAL flake", "add regression test", "none"),
        "Luis": ("sketched digest UI", "polish empty states", "review from Juan"),
        "David": ("synced the drive mirror", "archive stale folders", "none"),
    }

    # Act: all four members check in on the same date (real handler, real repos, real DB).
    for name, member_id in ids.items():
        done, nxt, blockers = first_pass[name]
        result = checkin_submit(
            {
                "member_id": member_id,
                "date": DAY,
                "done": done,
                "next": nxt,
                "blockers": blockers,
            },
            members,
            checkins,
            settings,
            clock,
        )
        assert result["ok"] is True

    # Act: Juan corrects his check-in for the SAME date (different done/next/blockers).
    corrected = checkin_submit(
        {
            "member_id": ids["Juan"],
            "date": DAY,
            "done": "wrote the ADR draft + pinned the DST cases",
            "next": "circulate for review tomorrow",
            "blockers": "none",
            "source": "manual",
        },
        members,
        checkins,
        settings,
        clock,
    )

    # Assert: handler ok; DB holds EXACTLY 4 rows and Juan's row is the corrected one.
    assert corrected["ok"] is True
    rows = checkins.by_date(DAY)
    assert len(rows) == 4
    juan_row = next(row for row in rows if row.member_id == ids["Juan"])
    assert juan_row.done == "wrote the ADR draft + pinned the DST cases"
    assert juan_row.next == "circulate for review tomorrow"
    assert juan_row.blockers == "none"
    assert juan_row.source == "manual"

    # Assert: the checkins_by_date handler returns all 4 rows for that date.
    listed = checkins_by_date({"date": DAY}, members, checkins, settings, clock)
    assert listed["ok"] is True
    data = listed["data"]
    assert isinstance(data, dict)
    view = data["checkins"]
    assert isinstance(view, list)
    assert len(view) == 4


def test_digest_settings_roundtrip(day: Day) -> None:
    """setting_set persists the three digest knobs and setting_get reads each back verbatim."""
    members, checkins, settings, clock = day
    knobs = {"digest_time": "17:00", "digest_chat": "dm", "nudge_limit": "2"}

    set_results = [
        setting_set({"key": key, "value": value}, members, checkins, settings, clock)
        for key, value in knobs.items()
    ]

    assert [result["ok"] for result in set_results] == [True, True, True]
    for key, value in knobs.items():
        readback = setting_get({"key": key}, members, checkins, settings, clock)
        assert readback["ok"] is True
        data = readback["data"]
        assert isinstance(data, dict)
        assert data["value"] == value


def test_dst_relays_pin_schedules_at_winter_and_summer(day: Day) -> None:
    """Wake-edit relays pin Guayaquil (no DST) equal at both instants and Berlin flipped 1h."""
    members, checkins, settings, clock = day
    ids = _member_ids(day)
    jose, juan = ids["Jose"], ids["Juan"]

    def wake_edit(member_id: int, wake: str) -> dict[str, object]:
        """Submit a real member_update wake change and return its ok handler result."""
        result = member_update(
            {"member_id": member_id, "wake": wake}, members, checkins, settings, clock
        )
        assert result["ok"] is True
        return result

    def relay_schedule(result: dict[str, object]) -> str:
        """Extract the pre-computed cron schedule string from a relay-carrying result."""
        relay = result["cron_relay"]
        assert isinstance(relay, dict)
        args = relay["args"]
        assert isinstance(args, dict)
        schedule = args["schedule"]
        assert isinstance(schedule, str)
        return schedule

    # Winter instant: away-and-back wake edits on both members; the back-edit relays the
    # stored wake resolved at THIS instant (Guayaquil on UTC-5, Berlin on CET UTC+1).
    clock.instant = WINTER
    assert relay_schedule(wake_edit(jose, "06:30")) == "30 11 * * *"  # 06:30 + 5h
    winter_guayaquil = wake_edit(jose, "06:00")  # restores the seeded wake, relays it
    assert winter_guayaquil["cron_relay"] == {
        "tool": "cronjob",
        "args": {"action": "edit", "name": f"checkin-{jose}", "schedule": "0 11 * * *"},
    }  # PIN: Guayaquil 06:00 @ winter
    assert relay_schedule(wake_edit(juan, "07:00")) == "0 6 * * *"  # 07:00 - 1h
    winter_berlin = wake_edit(juan, "06:30")
    assert relay_schedule(winter_berlin) == "30 5 * * *"  # PIN: Berlin 06:30 @ winter

    # Summer instant: same away-and-back mechanics; only Berlin's offset changed (+2h).
    clock.instant = SUMMER
    assert relay_schedule(wake_edit(jose, "06:30")) == "30 11 * * *"  # identical: no DST
    summer_guayaquil = wake_edit(jose, "06:00")
    assert relay_schedule(summer_guayaquil) == "0 11 * * *"  # PIN: Guayaquil 06:00 @ summer
    assert relay_schedule(summer_guayaquil) == relay_schedule(winter_guayaquil)
    assert relay_schedule(wake_edit(juan, "07:00")) == "0 5 * * *"  # 07:00 - 2h
    summer_berlin = wake_edit(juan, "06:30")
    assert relay_schedule(summer_berlin) == "30 4 * * *"  # PIN: Berlin 06:30 @ summer
    assert relay_schedule(summer_berlin) != relay_schedule(winter_berlin)
