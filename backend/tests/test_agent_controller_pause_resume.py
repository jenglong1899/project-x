import asyncio
import unittest

from src.core.agent_base import AgentBase, DriveDecision, DriveReason
from src.core.agent_runner import AgentRunner


class _FakeAgentPausedToolTurn(AgentBase):
    """
    模拟“工具执行完后暂停”的场景：
    - 第一次 run()：消费 1 条 user message，然后进入 paused，同时还欠一轮 follow-up（无新 user message）
    - 第二次 run()：生成 follow-up，清空 pending work
    """

    def __init__(self) -> None:
        self._queued_user_messages: list[tuple[str, str]] = []
        self._paused = False
        self._pause_requested = False
        self._pending_follow_up = False
        self.run_calls = 0

    def start_conversation(self) -> None:
        return None

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        self._queued_user_messages.append((frontend_msg_id, user_message))

    def request_pause(self) -> None:
        self._pause_requested = True

    def resume(self) -> None:
        self._pause_requested = False
        self._paused = False

    def is_paused(self) -> bool:
        return self._paused

    def is_pause_requested(self) -> bool:
        return self._pause_requested

    def _has_backlog(self) -> bool:
        return bool(self._queued_user_messages) or self._pending_follow_up

    def drive_decision(self) -> DriveDecision:
        if self._paused:
            reason = DriveReason.paused_with_backlog if self._has_backlog() else DriveReason.paused_no_backlog
            return DriveDecision(should_drive=False, reason=reason)
        if self._queued_user_messages:
            return DriveDecision(should_drive=True, reason=DriveReason.backlog_user_msg)
        if self._pending_follow_up:
            return DriveDecision(should_drive=True, reason=DriveReason.backlog_tool_followup)
        return DriveDecision(should_drive=False, reason=DriveReason.no_backlog)

    async def run(self) -> dict[str, object]:
        self.run_calls += 1

        # 第一次 run：模拟执行工具后在检查点暂停
        if self._queued_user_messages:
            self._queued_user_messages.pop(0)
            self._pending_follow_up = True
            self._paused = True
            return {"role": "assistant", "tool_calls": [{"id": "call_1"}]}

        # 第二次 run：补齐 follow-up
        self._pending_follow_up = False
        return {"role": "assistant", "content": "follow-up"}


class _FakeAgentPauseWithQueuedMessages(AgentBase):
    """
    模拟“已有排队 user message 的暂停”场景：
    - 第一次 run()：只处理 1 条 user message，然后进入 paused（队列里仍有剩余消息）
    - 第二次 run()：处理剩余消息并结束
    """

    def __init__(self) -> None:
        self._queued_user_messages: list[tuple[str, str]] = []
        self._paused = False
        self._pause_requested = False
        self.run_calls = 0

    def start_conversation(self) -> None:
        return None

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        self._queued_user_messages.append((frontend_msg_id, user_message))

    def request_pause(self) -> None:
        self._pause_requested = True

    def resume(self) -> None:
        self._pause_requested = False
        self._paused = False

    def is_paused(self) -> bool:
        return self._paused

    def is_pause_requested(self) -> bool:
        return self._pause_requested

    def _has_backlog(self) -> bool:
        return bool(self._queued_user_messages)

    def drive_decision(self) -> DriveDecision:
        if self._paused:
            reason = DriveReason.paused_with_backlog if self._has_backlog() else DriveReason.paused_no_backlog
            return DriveDecision(should_drive=False, reason=reason)
        if self._has_backlog():
            return DriveDecision(should_drive=True, reason=DriveReason.backlog_user_msg)
        return DriveDecision(should_drive=False, reason=DriveReason.no_backlog)

    async def run(self) -> dict[str, object]:
        self.run_calls += 1
        if not self._queued_user_messages:
            return {"role": "assistant", "content": "noop"}

        self._queued_user_messages.pop(0)
        # 模拟“turn 结束处 pause”——即使还有排队消息也必须停下
        if self._pause_requested:
            self._pause_requested = False
            self._paused = True
        return {"role": "assistant", "content": "done"}


class AgentRunnerPauseResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_paused_tool_turn_restarts_without_new_user_message(self) -> None:
        agent = _FakeAgentPausedToolTurn()
        idle_events: asyncio.Queue[None] = asyncio.Queue()
        runner = AgentRunner(
            agent=agent,
            is_closed=lambda: False,
            on_agent_became_busy=lambda: None,
            on_agent_turn_completed=lambda: None,
            on_agent_became_idle=lambda: idle_events.put_nowait(None),
            on_error=lambda _exc: None,
        )
        runner.start()

        runner.submit_user_message(frontend_msg_id="m1", user_message="hi")
        await asyncio.wait_for(idle_events.get(), timeout=1)
        self.assertEqual(agent.run_calls, 1)
        self.assertTrue(agent.is_paused())
        self.assertEqual(agent.drive_decision().reason, DriveReason.paused_with_backlog)

        runner.resume()
        await asyncio.wait_for(idle_events.get(), timeout=1)
        self.assertEqual(agent.run_calls, 2)
        self.assertFalse(agent.is_paused())
        self.assertEqual(agent.drive_decision().reason, DriveReason.no_backlog)

    async def test_pause_stops_runner_loop_even_with_queued_messages(self) -> None:
        agent = _FakeAgentPauseWithQueuedMessages()
        idle_events: asyncio.Queue[None] = asyncio.Queue()
        runner = AgentRunner(
            agent=agent,
            is_closed=lambda: False,
            on_agent_became_busy=lambda: None,
            on_agent_turn_completed=lambda: None,
            on_agent_became_idle=lambda: idle_events.put_nowait(None),
            on_error=lambda _exc: None,
        )
        runner.start()

        runner.submit_user_message(frontend_msg_id="m1", user_message="one")
        runner.submit_user_message(frontend_msg_id="m2", user_message="two")
        runner.request_pause()

        await asyncio.wait_for(idle_events.get(), timeout=1)
        self.assertEqual(agent.run_calls, 1)
        self.assertTrue(agent.is_paused())
        self.assertEqual(agent.drive_decision().reason, DriveReason.paused_with_backlog)

        runner.resume()
        await asyncio.wait_for(idle_events.get(), timeout=1)
        self.assertEqual(agent.run_calls, 2)
        self.assertFalse(agent.is_paused())
        self.assertEqual(agent.drive_decision().reason, DriveReason.no_backlog)
