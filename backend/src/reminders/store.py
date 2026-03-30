import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from typing import Literal

from src.reminders.schedule import IntervalSchedule, OneTimeSchedule


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(text: str) -> datetime:
    if not isinstance(text, str) or not text:
        raise ValueError("UTC 时间字符串不能为空")
    try:
        # python 支持解析 ...+00:00，也支持 Z 但需要手动处理
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"UTC 时间字符串解析失败: {text}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"UTC 时间字符串缺少时区信息: {text}")
    return dt.astimezone(timezone.utc)


def parse_utc_iso(text: str) -> datetime:
    return _parse_utc_iso(text)


@dataclass(frozen=True)
class ReminderRecord:
    name: str
    content: str
    schedule_kind: Literal["interval", "one_time"]
    interval_seconds: int | None
    one_time_utc: str | None
    next_fire_at_utc: str
    created_at_utc: str

    @staticmethod
    def new_interval(*, name: str, content: str, interval_seconds: int, now: datetime | None = None) -> "ReminderRecord":
        resolved_now = now or _utc_now()
        next_fire = resolved_now + timedelta(seconds=interval_seconds)
        return ReminderRecord(
            name=name,
            content=content,
            schedule_kind="interval",
            interval_seconds=interval_seconds,
            one_time_utc=None,
            next_fire_at_utc=_utc_iso(next_fire),
            created_at_utc=_utc_iso(resolved_now),
        )

    @staticmethod
    def new_one_time(
        *, name: str, content: str, one_time_utc: datetime, now: datetime | None = None
    ) -> "ReminderRecord":
        resolved_now = now or _utc_now()
        return ReminderRecord(
            name=name,
            content=content,
            schedule_kind="one_time",
            interval_seconds=None,
            one_time_utc=_utc_iso(one_time_utc),
            next_fire_at_utc=_utc_iso(one_time_utc),
            created_at_utc=_utc_iso(resolved_now),
        )


class ReminderStore:
    """
    v1 最小化 reminder 持久化：
    - 用一个 JSON 文件保存所有 reminder
    - 读写都在进程内加锁，避免 scheduler/tool 并发修改导致损坏
    """

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._reminders_by_name: dict[str, ReminderRecord] = {}
        self._load_if_exists()

    def list_names(self) -> list[str]:
        with self._lock:
            return sorted(self._reminders_by_name.keys())

    def list_all(self) -> list[ReminderRecord]:
        with self._lock:
            return list(self._reminders_by_name.values())

    def get(self, *, name: str) -> ReminderRecord | None:
        with self._lock:
            return self._reminders_by_name.get(name)

    def create_interval(self, *, name: str, content: str, schedule: IntervalSchedule) -> None:
        with self._lock:
            if name in self._reminders_by_name:
                raise ValueError(f"reminder 已存在: {name}")
            now = _utc_now()
            record = _build_interval_record(name=name, content=content, schedule=schedule, now=now)
            self._reminders_by_name[name] = record
            self._write_json_atomically()

    def create_one_time(self, *, name: str, content: str, schedule: OneTimeSchedule) -> None:
        with self._lock:
            if name in self._reminders_by_name:
                raise ValueError(f"reminder 已存在: {name}")
            now = _utc_now()
            record = _build_one_time_record(name=name, content=content, schedule=schedule, now=now)
            self._reminders_by_name[name] = record
            self._write_json_atomically()

    def delete(self, *, name: str) -> None:
        with self._lock:
            if name not in self._reminders_by_name:
                raise ValueError(f"reminder 不存在: {name}")
            self._reminders_by_name.pop(name, None)
            self._write_json_atomically()

    def delete_if_exists(self, *, name: str) -> bool:
        with self._lock:
            if name not in self._reminders_by_name:
                return False
            self._reminders_by_name.pop(name, None)
            self._write_json_atomically()
            return True

    def update_next_fire_at(self, *, name: str, next_fire_at_utc: datetime) -> None:
        with self._lock:
            record = self._reminders_by_name.get(name)
            if record is None:
                raise ValueError(f"reminder 不存在: {name}")
            updated = ReminderRecord(
                name=record.name,
                content=record.content,
                schedule_kind=record.schedule_kind,
                interval_seconds=record.interval_seconds,
                one_time_utc=record.one_time_utc,
                next_fire_at_utc=_utc_iso(next_fire_at_utc),
                created_at_utc=record.created_at_utc,
            )
            self._reminders_by_name[name] = updated
            self._write_json_atomically()

    def _load_if_exists(self) -> None:
        if not self._path.exists():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("reminders.json 顶层必须是 object")
        version = payload.get("version", 1)
        if version != 1:
            raise ValueError(f"reminders.json version 不受支持: {version}")
        reminders = payload.get("reminders", [])
        if not isinstance(reminders, list):
            raise ValueError("reminders.json.reminders 必须是数组")

        parsed: dict[str, ReminderRecord] = {}
        for item in reminders:
            if not isinstance(item, dict):
                continue
            record = _record_from_dict(item)
            if record.name:
                parsed[record.name] = record
        self._reminders_by_name = parsed

    def _write_json_atomically(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "reminders": [asdict(r) for r in self._reminders_by_name.values()],
        }
        temp_path = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self._path)


def _record_from_dict(item: dict[str, Any]) -> ReminderRecord:
    raw_kind = str(item.get("schedule_kind") or "")
    # 兼容早期草稿值：at/single_time -> one_time（下次写入会统一成 one_time）
    kind = "one_time" if raw_kind in {"at", "single_time"} else raw_kind
    if kind not in {"interval", "one_time"}:
        raise ValueError(f"reminder schedule_kind 非法: {raw_kind}")

    return ReminderRecord(
        name=str(item.get("name") or ""),
        content=str(item.get("content") or ""),
        schedule_kind=kind,  # type: ignore[arg-type]
        interval_seconds=int(item["interval_seconds"]) if item.get("interval_seconds") is not None else None,
        one_time_utc=(
            str(item.get("one_time_utc"))
            if item.get("one_time_utc") is not None
            else (
                str(item.get("single_time_utc"))
                if item.get("single_time_utc") is not None
                else (str(item.get("at_utc")) if item.get("at_utc") is not None else None)
            )
        ),
        next_fire_at_utc=str(item.get("next_fire_at_utc") or ""),
        created_at_utc=str(item.get("created_at_utc") or ""),
    )


def _build_interval_record(*, name: str, content: str, schedule: IntervalSchedule, now: datetime) -> ReminderRecord:
    next_fire = now + timedelta(seconds=schedule.interval_seconds)
    return ReminderRecord(
        name=name,
        content=content,
        schedule_kind="interval",
        interval_seconds=schedule.interval_seconds,
        one_time_utc=None,
        next_fire_at_utc=_utc_iso(next_fire),
        created_at_utc=_utc_iso(now),
    )


def _build_one_time_record(*, name: str, content: str, schedule: OneTimeSchedule, now: datetime) -> ReminderRecord:
    one_time_utc = schedule.one_time_utc.astimezone(timezone.utc)
    return ReminderRecord(
        name=name,
        content=content,
        schedule_kind="one_time",
        interval_seconds=None,
        one_time_utc=_utc_iso(one_time_utc),
        next_fire_at_utc=_utc_iso(one_time_utc),
        created_at_utc=_utc_iso(now),
    )
