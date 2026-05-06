import unittest
from unittest import mock

from src.commons import MEMORY_MAIN_MD
from src.core import prompts


class MemoryForkedSubagentPromptTests(unittest.TestCase):
    def test_first_awaken_does_not_read_or_include_memory_diff(self) -> None:
        with mock.patch("src.core.prompts.read_main_memory") as read_main_memory:
            prompt = prompts.build_memory_forked_subagent_prompt(
                is_first_time_awaken=True,
                loaded_main_memory_content="context memory",
            )

        read_main_memory.assert_not_called()
        self.assertNotIn("<memory_diff>", prompt)
        self.assertIn("第一次在当前会话中被唤醒", prompt)

    def test_not_first_awaken_includes_unified_main_memory_diff(self) -> None:
        with mock.patch(
            "src.core.prompts.read_main_memory",
            return_value="line 1\nline 2 changed\nline 3",
        ):
            prompt = prompts.build_memory_forked_subagent_prompt(
                is_first_time_awaken=False,
                loaded_main_memory_content="line 1\nline 2\nline 3",
            )

        self.assertIn("<memory_diff>", prompt)
        self.assertIn(f"--- context-{MEMORY_MAIN_MD}", prompt)
        self.assertIn(f"+++ disk-{MEMORY_MAIN_MD}", prompt)
        self.assertIn("-line 2", prompt)
        self.assertIn("+line 2 changed", prompt)

    def test_not_first_awaken_includes_no_difference_hint_when_memory_is_same(self) -> None:
        with mock.patch(
            "src.core.prompts.read_main_memory",
            return_value="same memory",
        ):
            prompt = prompts.build_memory_forked_subagent_prompt(
                is_first_time_awaken=False,
                loaded_main_memory_content="same memory",
            )

        self.assertIn("<memory_diff>", prompt)
        self.assertIn("(project-x-hint: no difference)", prompt)
        self.assertNotIn(f"--- context-{MEMORY_MAIN_MD}", prompt)


if __name__ == "__main__":
    unittest.main()
