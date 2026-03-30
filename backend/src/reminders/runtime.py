from __future__ import annotations

import asyncio
import threading

from src.commons import REMINDERS_PATH
from src.reminders.store import ReminderStore

# v1：以单例 store 作为进程内共享状态：
# - tool handler 与 scheduler 共用同一个 store（共享锁与内存视图）
# - 单测通过 monkeypatch/mock.patch 替换 `_STORE` 注入临时路径
_STORE = ReminderStore(path=REMINDERS_PATH)

_RUNTIME_LOCK = threading.Lock()
_SCHEDULER_LOOP: asyncio.AbstractEventLoop | None = None
_SCHEDULER_WAKEUP_EVENT: asyncio.Event | None = None


def get_store() -> ReminderStore:
    return _STORE


def bind_scheduler_wakeup(*, loop: asyncio.AbstractEventLoop, wakeup_event: asyncio.Event) -> None:
    with _RUNTIME_LOCK:
        global _SCHEDULER_LOOP, _SCHEDULER_WAKEUP_EVENT
        _SCHEDULER_LOOP = loop
        _SCHEDULER_WAKEUP_EVENT = wakeup_event


def unbind_scheduler_wakeup() -> None:
    with _RUNTIME_LOCK:
        global _SCHEDULER_LOOP, _SCHEDULER_WAKEUP_EVENT
        _SCHEDULER_LOOP = None
        _SCHEDULER_WAKEUP_EVENT = None


def notify_scheduler_changed() -> None:
    """
    当 tool 修改了 reminder store（create/delete）后调用，用于唤醒 scheduler 重新计算 next fire。

    说明：
    - tool handler 运行在 Agent.run() 的线程里（`asyncio.to_thread(...)`），不在 event loop 线程。
    - 因此这里用 `loop.call_soon_threadsafe(...)` 线程安全地 set asyncio.Event。
    """
    with _RUNTIME_LOCK:
        loop = _SCHEDULER_LOOP
        wakeup_event = _SCHEDULER_WAKEUP_EVENT
    if loop is None or wakeup_event is None:
        return
    loop.call_soon_threadsafe(wakeup_event.set)
