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


class _DelayedDeciderRunner:
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

                agent._summarizer_runner.run = mock.AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
                agent._decider_runner.run = mock.AsyncMock(return_value=False)  # type: ignore[method-assign]

                with mock.patch("src.core.agent.logger") as fake_logger:
                    await agent._maybe_wake_memory_manager(prompt_tokens=10_000)
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)

                    self.assertIsNotNone(agent._summarizer_task)
                    self.assertTrue(agent._summarizer_task.done())
                    fake_logger.error.assert_called()

    async def test_maybe_wake_memory_manager_does_not_wait_for_summarizer_or_decider(self) -> None:
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
                decider_runner = _DelayedDeciderRunner(should_reset_context=False)
                agent._summarizer_runner = summarizer_runner
                agent._decider_runner = decider_runner

                await agent._maybe_wake_memory_manager(prompt_tokens=10_000)
                await asyncio.sleep(0)

                self.assertTrue(summarizer_runner.started.is_set())
                self.assertTrue(decider_runner.started.is_set())
                self.assertFalse(agent._summarizer_task.done())
                self.assertFalse(agent._decider_task.done())

                summarizer_runner.allow_finish.set()
                decider_runner.allow_finish.set()
                await agent._summarizer_task
                await agent._decider_task

    async def test_delayed_decider_reset_pauses_worker_before_reset_when_worker_is_idle(self) -> None:
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

                decider_runner = _DelayedDeciderRunner(should_reset_context=True)
                agent._summarizer_runner.run = mock.AsyncMock(return_value=None)  # type: ignore[method-assign]
                agent._decider_runner = decider_runner
                original_reset_context = agent._reset_context
                agent._reset_context = lambda *, carryover_messages: (  # type: ignore[method-assign]
                    reset_observations.append(agent.is_paused()),
                    original_reset_context(carryover_messages=carryover_messages),
                )[-1]

                with mock.patch("src.core.init_prompts.build_init_messages", return_value=[{"role": "user", "content": "reset init"}]):
                    await agent._maybe_wake_memory_manager(prompt_tokens=10_000)
                    decider_runner.allow_finish.set()
                    await asyncio.sleep(0)
                    await asyncio.sleep(0.1)

        self.assertEqual(paused_events, ["paused"])
        self.assertEqual(reset_observations, [True])

    async def test_delayed_decider_reset_waits_for_summarizer_when_worker_is_idle(self) -> None:
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
                decider_runner = _DelayedDeciderRunner(should_reset_context=True)
                agent._summarizer_runner = summarizer_runner
                agent._decider_runner = decider_runner

                with mock.patch("src.core.init_prompts.build_init_messages", return_value=[{"role": "user", "content": "reset init"}]):
                    await agent._maybe_wake_memory_manager(prompt_tokens=10_000)
                    await summarizer_runner.started.wait()
                    decider_runner.allow_finish.set()
                    await asyncio.sleep(0.1)

                    self.assertIsNotNone(agent._memory_manager_reset_task)
                    self.assertFalse(agent._memory_manager_reset_task.done())

                    summarizer_runner.allow_finish.set()
                    await asyncio.sleep(0)
                    await asyncio.sleep(0.1)

        self.assertIsNotNone(agent._memory_manager_reset_task)
        self.assertTrue(agent._memory_manager_reset_task.done())

    async def test_reset_carryover_keeps_messages_after_latest_summary_flag(self) -> None:
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
                agent.enqueue_user_message(frontend_msg_id="1", user_message="before-flag")
                agent._safe_drain_user_message_queue()
                agent._append_runtime_message({"role": "user", "content": "WAKE_MM_SUMMARY_FLAG"})
                agent._append_runtime_message({"role": "assistant", "content": "after-flag"})
                agent._append_runtime_message({"role": "tool", "tool_call_id": "call_1", "content": "tool-result"})

                with mock.patch("src.core.agent.WAKE_MM_SUMMARY_FLAG", "WAKE_MM_SUMMARY_FLAG"):
                    carryover_messages = agent._build_reset_carryover_messages()

                self.assertEqual(
                    carryover_messages,
                    [
                        {"role": "assistant", "content": "after-flag"},
                        {"role": "tool", "tool_call_id": "call_1", "content": "tool-result"},
                    ],
                )


if __name__ == "__main__":
    unittest.main()
