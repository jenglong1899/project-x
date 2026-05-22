import unittest
from unittest import mock

from src.core.memory_manager import MemoryManagerJudgeResetContextRunner, RESET_CONTEXT_MAGIC_WORD
from src.core.model_config import ModelConfig


class MemoryManagerJudgeRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def _run_with_final_content(self, content: object) -> bool:
        runner = MemoryManagerJudgeResetContextRunner()

        with mock.patch(
            "src.core.memory_manager.stream",
            new=mock.AsyncMock(return_value={"role": "assistant", "content": content}),
        ):
            requested = await runner.run(
                worker_messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "user instruction"},
                ],
                model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                tools=[],
                conversation_file_name="calm-river-20260429T120000000000Z.json",
                awaken_round=1,
            )

        return requested

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
