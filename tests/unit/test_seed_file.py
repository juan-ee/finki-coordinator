"""Tests for the committed seed file: parses, timezones resolve, wakes well-formed.

Seed hygiene (v6, D3): the committed seed is a placeholder example — real Telegram
IDs are never committed; every telegram_id in the file must be None.
"""

import re
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "members.seed.yaml"


def _members() -> list[dict[str, object]]:
    """Load the committed seed file as a list of member mappings."""
    raw = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list), "seed file must be a YAML list of member mappings"
    return raw


def test_seed_parses_with_the_specified_roster_shape() -> None:
    """Seed parses into 4 members: 3x America/Guayaquil and 1x Europe/Berlin."""
    members = _members()
    assert len(members) == 4
    timezones = [member["timezone"] for member in members]
    assert timezones.count("America/Guayaquil") == 3
    assert timezones.count("Europe/Berlin") == 1


def test_every_timezone_resolves_via_zoneinfo() -> None:
    """Every member timezone is a resolvable IANA zone on this machine."""
    for member in _members():
        ZoneInfo(str(member["timezone"]))  # raises ZoneInfoNotFoundError if bogus


def test_wakes_match_the_strict_hhmm_pattern() -> None:
    """Every wake matches ^([01]\\d|2[0-3]):[0-5]\\d$ and the founding team's wakes."""
    wakes = []
    for member in _members():
        wake = str(member["wake"])
        assert re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", wake), f"bad wake: {wake}"
        wakes.append(wake)
    assert sorted(wakes) == ["05:30", "06:00", "06:30", "11:00"]


def test_telegram_ids_are_placeholders() -> None:
    """Seed hygiene (D3): every committed telegram_id is None — real IDs never committed.

    Real Telegram IDs enter the system through scripts/allow.sh (the .env door) and
    member_add/member_update (session context), never through this file. A pre-known
    founding roster with real IDs belongs in a gitignored local seed file instead.
    """
    for member in _members():
        telegram_id = member.get("telegram_id")
        assert telegram_id is None, (
            "committed seed must carry placeholder telegram_ids only; found "
            f"{telegram_id!r} for {member.get('name')!r} — real IDs are never committed"
        )
