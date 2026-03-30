from datetime import datetime, timezone

import pytest

from src.reminders.schedule import parse_interval_seconds, parse_one_time_utc_minute


def test_parse_interval_seconds_valid() -> None:
    assert parse_interval_seconds("10sec") == 10
    assert parse_interval_seconds("5min") == 5 * 60
    assert parse_interval_seconds("1hour-30min") == 1 * 3600 + 30 * 60
    assert parse_interval_seconds("1day-2hour-10min-6sec") == 24 * 3600 + 2 * 3600 + 10 * 60 + 6
    assert parse_interval_seconds("1month") == 30 * 24 * 3600
    assert parse_interval_seconds("1year") == 365 * 24 * 3600


@pytest.mark.parametrize(
    "interval",
    [
        "",
        " 5min",
        "5min ",
        "5 min",
        "0min",
        "5mins",
        "1hour-30min-30min",
        "30min-1hour",
        "1hour--30min",
        "-1hour",
        "1hour-",
    ],
)
def test_parse_interval_seconds_invalid(interval: str) -> None:
    with pytest.raises(ValueError):
        parse_interval_seconds(interval)


def test_parse_at_utc_minute() -> None:
    dt = parse_one_time_utc_minute("2026-03-30T01:23Z")
    assert dt == datetime(2026, 3, 30, 1, 23, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "at_utc",
    ["", "2026-03-30 01:23Z", "2026-03-30T01:23", "2026-03-30T01:23:00Z", "2026-13-30T01:23Z"],
)
def test_parse_at_utc_minute_invalid(at_utc: str) -> None:
    with pytest.raises(ValueError):
        parse_one_time_utc_minute(at_utc)
