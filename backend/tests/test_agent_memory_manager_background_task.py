import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.agent import Agent
from src.conversation_store import ConversationStore
from src.core.model_config import ModelConfig


class _FakeTokenCounter:
    def __init__(self, *, context_window: int = 100, token_per_message: int = 10) -> None:
        self._context_window = context_window
        self._token_per_message = token_per_message

    def context_window(self, model: str) -> int:  # noqa: ARG002
        return self._context_window

    def count_messages_tokens(self, model: str, messages: list[dict[str, object]]) -> tuple[int, bool]:  # noqa: ARG002
        return len(messages) * self._token_per_message, True


class AgentMemoryManagerBackgroundTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_task_exception_is_observed_and_logged(self) -> None:
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
                    token_counter=_FakeTokenCounter(),
                )
                agent.start_conversation()
                agent.enqueue_user_message(frontend_msg_id="1", user_message="hi")
                agent._safe_drain_user_message_queue()
                agent._require_conversation_store().update_memory_manager_last_triggered_threshold(last_triggered_threshold=0)

                agent._memory_manager_summary_runner.run = mock.AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
                agent._memory_manager_judge_runner.run = mock.AsyncMock(return_value=False)  # type: ignore[method-assign]

                with mock.patch("src.core.agent.logger") as fake_logger:
                    await agent._maybe_wake_memory_manager()
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)

                    self.assertIsNotNone(agent._memory_manager_summary_task)
                    self.assertTrue(agent._memory_manager_summary_task.done())
                    fake_logger.error.assert_called()


if __name__ == "__main__":
    unittest.main()
