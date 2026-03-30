from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.core.agent_turn import ToolSpec
from src.reminders.runtime import get_store, notify_scheduler_changed
from src.reminders.schedule import (
    MIN_INTERVAL_SECONDS,
    IntervalSchedule,
    OneTimeSchedule,
    parse_interval_seconds,
    parse_one_time_utc_minute,
)

MAX_ACTIVE_REMINDERS = 100
MAX_NAME_LEN = 64
MAX_CONTENT_LEN = 4000
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IntervalOpts(BaseModel):
    kind: Literal["interval"] = "interval"
    interval: str = Field(min_length=1, description="间隔触发，例如：1hour-30min（不允许空格）")


class OneTimeOpts(BaseModel):
    kind: Literal["one_time"] = "one_time"
    one_time_utc: str = Field(min_length=1, description="一次性触发 UTC 时间：YYYY-MM-DDTHH:mmZ")


class CreateReminderInput(BaseModel):
    reminder_name: str = Field(min_length=1, max_length=MAX_NAME_LEN, description="reminder 的唯一名字")
    reminder_content: str = Field(min_length=1, max_length=MAX_CONTENT_LEN, description="reminder 的内容")
    opts: Annotated[IntervalOpts | OneTimeOpts, Field(discriminator="kind")]

    @model_validator(mode="after")
    def _validate_name(self) -> "CreateReminderInput":
        if not NAME_RE.match(self.reminder_name):
            raise ValueError("reminder_name 非法：只允许 a-zA-Z0-9_-，且长度 1~64")
        return self


class DeleteReminderInput(BaseModel):
    reminder_name: str = Field(min_length=1, max_length=MAX_NAME_LEN, description="要删除的 reminder 名字")

    @model_validator(mode="after")
    def _validate_name(self) -> "DeleteReminderInput":
        if not NAME_RE.match(self.reminder_name):
            raise ValueError("reminder_name 非法：只允许 a-zA-Z0-9_-，且长度 1~64")
        return self


def _create_reminder(*, arguments: dict[str, Any]) -> str:
    tool_input = CreateReminderInput.model_validate(arguments)
    store = get_store()

    # 防呆：限制 reminder 数量，避免模型无限创建把磁盘/内存打爆。
    if len(store.list_names()) >= MAX_ACTIVE_REMINDERS:
        raise ValueError(f"reminder 数量过多：最大 {MAX_ACTIVE_REMINDERS}")

    if isinstance(tool_input.opts, IntervalOpts):
        interval_seconds = parse_interval_seconds(tool_input.opts.interval)
        if interval_seconds < MIN_INTERVAL_SECONDS:
            # 双保险：parse_interval_seconds 已校验，这里再兜底一次，避免后续改动遗漏。
            raise ValueError(f"interval 过小：最小间隔为 {MIN_INTERVAL_SECONDS}sec")
        store.create_interval(
            name=tool_input.reminder_name,
            content=tool_input.reminder_content,
            schedule=IntervalSchedule(interval_seconds=interval_seconds),
        )
        notify_scheduler_changed()
        return "OK"

    # one_time 逻辑
    one_time = parse_one_time_utc_minute(tool_input.opts.one_time_utc)
    now = _utc_now()
    if one_time < now - timedelta(seconds=60):
        raise ValueError("one_time_utc 过于过去：超过 60 秒的过去时间不允许创建")
    if one_time <= now:
        one_time = now

    store.create_one_time(
        name=tool_input.reminder_name,
        content=tool_input.reminder_content,
        schedule=OneTimeSchedule(one_time_utc=one_time),
    )
    notify_scheduler_changed()
    return "OK"


def _delete_reminder(*, arguments: dict[str, Any]) -> str:
    tool_input = DeleteReminderInput.model_validate(arguments)
    store = get_store()
    store.delete(name=tool_input.reminder_name)
    notify_scheduler_changed()
    return "OK"


CREATE_REMINDER_TOOL = ToolSpec(
    name="create_reminder",
    description="创建一个 reminder（间隔触发 interval 或一次性触发 one_time_utc）。",
    parameters_json_schema=CreateReminderInput.model_json_schema(),
    handler=_create_reminder,
)

DELETE_REMINDER_TOOL = ToolSpec(
    name="delete_reminder",
    description="删除一个 reminder（不存在会报错）。",
    parameters_json_schema=DeleteReminderInput.model_json_schema(),
    handler=_delete_reminder,
)

REMINDER_TOOLS = [CREATE_REMINDER_TOOL, DELETE_REMINDER_TOOL]
