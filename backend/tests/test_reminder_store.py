from pathlib import Path

import pytest

from src.reminders.schedule import IntervalSchedule, OneTimeSchedule, parse_one_time_utc_minute
from src.reminders.store import ReminderStore


def test_reminder_store_create_delete(tmp_path: Path) -> None:
    path = tmp_path / "reminders.json"
    store = ReminderStore(path=path)

    store.create_interval(
        name="foo",
        content="bar",
        schedule=IntervalSchedule(interval_seconds=60),
    )
    assert store.get(name="foo") is not None

    store.delete(name="foo")
    assert store.get(name="foo") is None


def test_reminder_store_unique_name(tmp_path: Path) -> None:
    path = tmp_path / "reminders.json"
    store = ReminderStore(path=path)
    store.create_interval(
        name="same",
        content="x",
        schedule=IntervalSchedule(interval_seconds=60),
    )
    with pytest.raises(ValueError):
        store.create_one_time(
            name="same",
            content="y",
            schedule=OneTimeSchedule(one_time_utc=parse_one_time_utc_minute("2026-03-30T01:23Z")),
        )


def test_reminder_store_delete_missing(tmp_path: Path) -> None:
    path = tmp_path / "reminders.json"
    store = ReminderStore(path=path)
    with pytest.raises(ValueError):
        store.delete(name="missing")
