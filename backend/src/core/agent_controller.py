import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from src.commons import noop


class AgentLike(Protocol):
    def new_conversation(self) -> None: ...

    def resume_conversation(self, *, conversation_id: str) -> None: ...

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None: ...

    async def run(self) -> dict[str, Any]: ...

    def has_pending_user_messages(self) -> bool: ...


class AgentController:
    """
    AgentController 负责“确保 agent 在后台运行、避免重入、并持续运行直到 idle”。

    说明：
    - 这里不把 asyncio task 生命周期塞进 Agent 本体（保持 Agent 为“状态机 + 回调”）
    - 未来不同适配层（WebSocket/CLI/SSE 等）可以复用同一套驱动逻辑
    """

    def __init__(
        self,
        *,
        agent: AgentLike,
        is_closed: Callable[[], bool],
        on_agent_became_busy: Callable[[], None] | None = None,
        on_agent_became_idle: Callable[[], None] | None = None,
        on_agent_turn_completed: Callable[[], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._agent = agent
        self._is_closed = is_closed
        self._on_agent_became_busy: Callable[[], None] = on_agent_became_busy or noop
        self._on_agent_became_idle: Callable[[], None] = on_agent_became_idle or noop
        self._on_agent_turn_completed: Callable[[], None] = on_agent_turn_completed or noop
        self._on_error: Callable[[Exception], None] = on_error or noop

        self._task: asyncio.Task[None] | None = None

    def start(self, *, conversation_id: str | None) -> None:
        """
        初始化对话状态（必须先调用，否则 agent.run 可能因为 conversation_store 未初始化而报错）。

        约束：
        - controller 忙（task 正在跑）时不允许切换会话。
        """
        if self._task is not None and not self._task.done():
            raise RuntimeError("AgentController 忙，不能切换会话")

        if conversation_id:
            self._agent.resume_conversation(conversation_id=conversation_id)
        else:
            self._agent.new_conversation()

    def submit_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        self._agent.enqueue_user_message(
            frontend_msg_id=frontend_msg_id,
            user_message=user_message,
        )
        self._ensure_running()

    def _ensure_running(self) -> None:
        """
        确保后台 runner 已启动：
        - 如果当前没有 task，或 task 已结束，则启动新的 task
        - 如果 task 正在运行，则不做任何事（防重入）
        """
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_until_idle())

    async def _run_until_idle(self) -> None:
        # ensure_running这个函数里面的代码`self._task = asyncio.create_task(self._run_until_idle())`代表基于本函数创建一个task， current_task()就是取这个“基于本函数的task”
        # 这里用 current_task 做“只清理自己”的保护，避免竞态：
        # - 旧 task（A）在 finally 里准备清理 self._task
        # - 同时外部又调用 ensure_running()，创建了新 task（B）并写入 self._task
        # 如果 A 无脑把 self._task 置空，会误清理掉 B 的引用，导致 runner 状态错乱。
        current_task = asyncio.current_task()
        self._on_agent_became_busy()
        try:
            while True:
                await self._agent.run()
                self._on_agent_turn_completed()

                if self._is_closed():
                    return
                if not self._agent.has_pending_user_messages():
                    return
        except Exception as exc:
            self._on_error(exc)
        finally:
            self._on_agent_became_idle()
            if self._task is current_task:
                self._task = None
