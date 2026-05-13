import unittest
from src.core import init_prompts


class MemoryForkedSubagentPromptTests(unittest.TestCase):
    def test_first_awaken_does_not_read_or_include_memory_diff(self) -> None:
        prompt = init_prompts.build_memory_forked_subagent_prompt(
            is_first_time_awaken=True,
        )
        self.assertIn("第一次在当前会话中被唤醒", prompt)

    def test_not_first_awaken_includes_history_hint(self) -> None:
        prompt = init_prompts.build_memory_forked_subagent_prompt(
            is_first_time_awaken=False,
        )
        self.assertIn("不是你第一次在当前会话中被唤醒", prompt)


if __name__ == "__main__":
    unittest.main()
