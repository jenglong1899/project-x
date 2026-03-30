from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from src.reminders.store import ReminderRecord, ReminderStore, parse_utc_iso

logger = logging.getLogger(__name__)


OnReminderFired = Callable[[ReminderRecord], Awaitable[None]]


@dataclass(frozen=True)
class ReminderSchedulerConfig:
    max_sleep_seconds: float = 3600.0


class ReminderScheduler:
    def __init__(
        self,
        *,
        store: ReminderStore,
        on_reminder_fired: OnReminderFired,
        config: ReminderSchedulerConfig | None = None,
        wakeup_event: asyncio.Event | None = None,
    ) -> None:
        self._store = store
        self._on_reminder_fired = on_reminder_fired
        self._config = config or ReminderSchedulerConfig()
        self._wakeup_event = wakeup_event or asyncio.Event()

    async def run_forever(self) -> None:
        while True:
            next_fire_at = await self._drain_due_reminders()
            await self._sleep_until(next_fire_at)

    async def _drain_due_reminders(self) -> datetime | None:
        now = datetime.now(timezone.utc)
        reminders = self._store.list_all()
        if not reminders:
            return None

        next_fire_at: datetime | None = None
        for record in reminders:
            try:
                next_fire = parse_utc_iso(record.next_fire_at_utc)
            except Exception:
                logger.exception("reminder next_fire_at_utc 解析失败，已跳过: %s", record.name)
                continue

            if next_fire > now:
                if next_fire_at is None or next_fire < next_fire_at:
                    next_fire_at = next_fire
                continue

            # 先尝试投递；投递成功后再更新/删除，避免“投递失败但 record 被消费”。
            try:
                await self._on_reminder_fired(record)
            except Exception:
                logger.exception("reminder 投递失败，将在下次 tick 重试: %s", record.name)
                continue

            if record.schedule_kind == "interval":
                if not record.interval_seconds:
                    logger.error("interval reminder 缺少 interval_seconds，已删除: %s", record.name)
                    self._store.delete_if_exists(name=record.name)
                    continue

                next_fire_at = now + timedelta(seconds=int(record.interval_seconds))
                try:
                    self._store.update_next_fire_at(name=record.name, next_fire_at_utc=next_fire_at)
                except Exception:
                    logger.exception("reminder 更新 next_fire_at 失败: %s", record.name)
                continue

            # one_time：触发一次后删除
            if record.schedule_kind == "one_time":
                self._store.delete_if_exists(name=record.name)
                continue

            logger.error("未知 schedule_kind，已跳过: %s %s", record.name, record.schedule_kind)

        return next_fire_at

    async def _sleep_until(self, next_fire_at: datetime | None) -> None:
        """
        休眠策略：
        - 如果没有任何 reminder：等待 wakeup_event（由 tool create/delete 触发）。
        - 如果有 next_fire_at：sleep until next_fire_at；但若期间 wakeup_event 被 set，则提前醒来重新计算。
        """
        self._wakeup_event.clear()
        if next_fire_at is None:
            await self._wakeup_event.wait()
            return

        now = datetime.now(timezone.utc)
        delay = (next_fire_at - now).total_seconds()
        if delay <= 0:
            return

        delay = min(delay, self._config.max_sleep_seconds)
        try:
            await asyncio.wait_for(self._wakeup_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return
