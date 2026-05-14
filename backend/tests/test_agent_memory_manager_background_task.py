import asyncio
import unittest
from unittest import mock

from src.core.agent import Agent
from src.core.model_config import ModelConfig


class AgentMemoryManagerBackgroundTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_task_exception_is_observed_and_logged(self) -> None:
        agent = Agent(
            name="demo-agent",
            model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
            system_instruction="system",
            user_instruction="user instruction",
            tools=[],
            memory_manager_turn_interval=1,
        )
        agent.start_conversation()
        agent.enqueue_user_message(frontend_msg_id="1", user_message="hi")
        agent._safe_drain_user_message_queue()

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
