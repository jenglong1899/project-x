import unittest
from unittest import mock

from src.core.memory_manager import MemoryForkedSubagentRunner, RESET_CONTEXT_MAGIC_WORD
from src.core.model_config import ModelConfig


class MemoryForkedSubagentRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def _run_with_final_content(self, content: object) -> bool:
        runner = MemoryForkedSubagentRunner()

        with mock.patch(
            "src.core.memory_manager.stream",
            new=mock.AsyncMock(return_value={"role": "assistant", "content": content}),
        ):
            result = await runner.run(
                worker_messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "user instruction"},
                ],
                model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                tools=[],
                is_first_time_awaken=True,
            )

        return result.requested_reset_context

    async def test_magic_word_on_independent_line_requests_reset_context(self) -> None:
        requested = await self._run_with_final_content(
            f"记忆已经整理好了\n{RESET_CONTEXT_MAGIC_WORD}\n"
        )

        self.assertTrue(requested)

    async def test_magic_word_inside_sentence_does_not_request_reset_context(self) -> None:
        requested = await self._run_with_final_content(
            f"我只是提到了 {RESET_CONTEXT_MAGIC_WORD}，但它不是独立一行。"
        )

        self.assertFalse(requested)

    async def test_non_text_content_does_not_request_reset_context(self) -> None:
        requested = await self._run_with_final_content(None)

        self.assertFalse(requested)


if __name__ == "__main__":
    unittest.main()
