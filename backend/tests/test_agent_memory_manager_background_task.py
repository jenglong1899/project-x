import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.agent import Agent
from src.conversation_store import ConversationStore
from src.core.agent_turn import TurnUsage
from src.core.model_config import ModelConfig


class _BlockingSummarizerRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.allow_finish = asyncio.Event()

    async def run(self, **kwargs: object) -> None:  # noqa: ARG002
        self.started.set()
        await self.allow_finish.wait()


class _DelayedJudgeRunner:
    def __init__(self, *, should_reset_context: bool) -> None:
        self.should_reset_context = should_reset_context
        self.started = asyncio.Event()
        self.allow_finish = asyncio.Event()

    async def run(self, **kwargs: object) -> bool:  # noqa: ARG002
        self.started.set()
        await self.allow_finish.wait()
        return self.should_reset_context


class AgentMemoryManagerBackgroundTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_summarizer_task_exception_is_observed_and_logged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("src.core.agent.ConversationStore") as store_cls:
                store_cls.find_latest_conversation_file_name.return_value = None
                store_cls.side_effect = lambda *, init_messages: ConversationStore(
                    init_messages=init_messages,
                    originals_dir=Path(temp_dir),
                )
                agent = Agent(
                    name="demo-agent",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user instruction"}],
                    tools=[],
                )
                agent.start_conversation()
                agent.enqueue_user_message(frontend_msg_id="1", user_message="hi")
                agent._safe_drain_user_message_queue()
                agent._require_conversation_store().update_memory_manager_last_triggered_threshold(last_triggered_threshold=0)

                agent._memory_manager_summarizer_runner.run = mock.AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
                agent._memory_manager_judge_runner.run = mock.AsyncMock(return_value=False)  # type: ignore[method-assign]

                with mock.patch("src.core.agent.logger") as fake_logger:
                    await agent._maybe_wake_memory_manager(prompt_tokens=10_000)
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)

                    self.assertIsNotNone(agent._memory_manager_summarizer_task)
                    self.assertTrue(agent._memory_manager_summarizer_task.done())
                    fake_logger.error.assert_called()

    async def test_maybe_wake_memory_manager_does_not_wait_for_summarizer_or_judge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("src.core.agent.ConversationStore") as store_cls:
                store_cls.find_latest_conversation_file_name.return_value = None
                store_cls.side_effect = lambda *, init_messages: ConversationStore(init_messages=init_messages, originals_dir=Path(temp_dir))
                agent = Agent(
                    name="demo-agent",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user instruction"}],
                    tools=[],
                )
                agent.start_conversation()
                agent.enqueue_user_message(frontend_msg_id="1", user_message="hi")
                agent._safe_drain_user_message_queue()
                agent._require_conversation_store().update_memory_manager_last_triggered_threshold(last_triggered_threshold=0)

                summarizer_runner = _BlockingSummarizerRunner()
                judge_runner = _DelayedJudgeRunner(should_reset_context=False)
                agent._memory_manager_summarizer_runner = summarizer_runner
                agent._memory_manager_judge_runner = judge_runner

                await agent._maybe_wake_memory_manager(prompt_tokens=10_000)
                await asyncio.sleep(0)

                self.assertTrue(summarizer_runner.started.is_set())
                self.assertTrue(judge_runner.started.is_set())
                self.assertFalse(agent._memory_manager_summarizer_task.done())
                self.assertFalse(agent._memory_manager_judge_task.done())

                summarizer_runner.allow_finish.set()
                judge_runner.allow_finish.set()
                await agent._memory_manager_summarizer_task
                await agent._memory_manager_judge_task

    async def test_delayed_judge_reset_pauses_worker_before_reset_when_worker_is_idle(self) -> None:
        paused_events: list[str] = []
        reset_observations: list[bool] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("src.core.agent.ConversationStore") as store_cls:
                store_cls.find_latest_conversation_file_name.return_value = None
                store_cls.side_effect = lambda *, init_messages: ConversationStore(init_messages=init_messages, originals_dir=Path(temp_dir))
                agent = Agent(
                    name="demo-agent",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user instruction"}],
                    tools=[],
                    on_paused=lambda: paused_events.append("paused"),
                )
                agent.start_conversation()
                agent.enqueue_user_message(frontend_msg_id="1", user_message="hi")
                agent._safe_drain_user_message_queue()
                agent._require_conversation_store().update_memory_manager_last_triggered_threshold(last_triggered_threshold=0)

                judge_runner = _DelayedJudgeRunner(should_reset_context=True)
                agent._memory_manager_summarizer_runner.run = mock.AsyncMock(return_value=None)  # type: ignore[method-assign]
                agent._memory_manager_judge_runner = judge_runner
                original_reset_context = agent._reset_context
                agent._reset_context = lambda: (reset_observations.append(agent.is_paused()), original_reset_context())[-1]  # type: ignore[method-assign]

                with mock.patch("src.core.init_prompts.build_init_messages", return_value=[{"role": "user", "content": "reset init"}]):
                    await agent._maybe_wake_memory_manager(prompt_tokens=10_000)
                    judge_runner.allow_finish.set()
                    await asyncio.sleep(0)
                    await asyncio.sleep(0.1)

        self.assertEqual(paused_events, ["paused"])
        self.assertEqual(reset_observations, [True])


if __name__ == "__main__":
    unittest.main()
