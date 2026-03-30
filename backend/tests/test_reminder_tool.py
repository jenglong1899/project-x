from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.reminders.store import ReminderStore
from src.reminders import runtime as reminders_runtime
from src.tools import reminder as reminder_tools


def test_create_reminder_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reminders_path = tmp_path / "reminders.json"
    monkeypatch.setattr(reminders_runtime, "_STORE", ReminderStore(path=reminders_path))
    create_tool = reminder_tools.CREATE_REMINDER_TOOL

    out = create_tool.handler(
        arguments={
            "reminder_name": "wake_up",
            "reminder_content": "do something",
            "opts": {"kind": "interval", "interval": "10sec"},
        }
    )
    assert out == "OK"

    store = ReminderStore(path=reminders_path)
    record = store.get(name="wake_up")
    assert record is not None
    assert record.schedule_kind == "interval"


def test_create_reminder_one_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reminders_path = tmp_path / "reminders.json"
    monkeypatch.setattr(reminders_runtime, "_STORE", ReminderStore(path=reminders_path))
    create_tool = reminder_tools.CREATE_REMINDER_TOOL

    future = datetime.now(timezone.utc) + timedelta(minutes=2)
    future_text = future.strftime("%Y-%m-%dT%H:%MZ")

    out = create_tool.handler(
        arguments={
            "reminder_name": "once",
            "reminder_content": "check",
            "opts": {"kind": "one_time", "one_time_utc": future_text},
        }
    )
    assert out == "OK"

    store = ReminderStore(path=reminders_path)
    record = store.get(name="once")
    assert record is not None
    assert record.schedule_kind == "one_time"
    assert isinstance(record.one_time_utc, str) and record.one_time_utc


def test_delete_reminder_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reminders_path = tmp_path / "reminders.json"
    monkeypatch.setattr(reminders_runtime, "_STORE", ReminderStore(path=reminders_path))
    delete_tool = reminder_tools.DELETE_REMINDER_TOOL

    with pytest.raises(ValueError):
        delete_tool.handler(arguments={"reminder_name": "missing"})
