"""Schedule and option helpers."""

import pytest

from custom_components.night_watchman import (
    _as_list,
    _last_due_minute,
    _minutes,
    _needs_immediate_notice,
    _parse_time,
    _positive_int,
    _unique,
    round_is_due,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("23:30", (23, 30)),
        ("23:30:00", (23, 30)),
        ("7", (7, 0)),
        ({"hour": 1, "minute": 15}, (1, 15)),
        ("24:00", None),
        ("12:60", None),
        ("noon", None),
        (None, None),
    ],
)
def test_parse_time(value, expected):
    assert _parse_time(value) == expected


def test_minutes():
    assert _minutes("01:30") == 90
    assert _minutes("bad") is None


def test_round_due_same_day_window():
    start, end = _minutes("01:00"), _minutes("05:00")
    assert round_is_due(_minutes("01:00"), start, end, 30)
    assert round_is_due(_minutes("01:30"), start, end, 30)
    assert not round_is_due(_minutes("01:15"), start, end, 30)
    assert not round_is_due(_minutes("05:30"), start, end, 30)


def test_round_due_window_crossing_midnight():
    start, end = _minutes("23:00"), _minutes("02:00")
    assert round_is_due(_minutes("23:00"), start, end, 60)
    assert round_is_due(_minutes("00:00"), start, end, 60)
    assert round_is_due(_minutes("02:00"), start, end, 60)
    assert not round_is_due(_minutes("00:30"), start, end, 60)
    assert not round_is_due(_minutes("12:00"), start, end, 60)


def test_round_never_due_with_zero_interval():
    assert not round_is_due(60, 0, 120, 0)
    assert _last_due_minute(0, 120, 0) is None


def test_last_due_minute():
    assert _last_due_minute(_minutes("01:00"), _minutes("05:10"), 30) == _minutes("05:00")
    assert _last_due_minute(_minutes("23:00"), _minutes("02:10"), 60) == _minutes("02:00")


def test_list_helpers():
    assert _as_list(None) == []
    assert _as_list("light.a") == ["light.a"]
    assert _as_list(["light.a", "", 3, "light.b"]) == ["light.a", "light.b"]
    assert _unique(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_positive_int():
    assert _positive_int("15", 30) == 15
    assert _positive_int("0", 30) == 30
    assert _positive_int(None, 30) == 30


def test_immediate_notice_only_for_problems():
    assert _needs_immediate_notice("Lock failed: Front Door")
    assert _needs_immediate_notice("Round failed. Check the Home Assistant log.")
    assert not _needs_immediate_notice("All clear")
