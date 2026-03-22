import json
import tempfile
import unittest
from pathlib import Path

from src.conversation_store import ConversationStore, truncate_display_name


class ConversationStoreTests(unittest.TestCase):
    def test_start_with_first_user_message_creates_json_with_truncated_display_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(
                system_instruction="system",
                user_instruction="memory",
                originals_dir=Path(temp_dir),
            )

            store.start_with_first_user_message(user_content="第一条用户消息已经超过二十个字符限制了并且后面还有内容")

            stored_files = list(Path(temp_dir).glob("*.json"))
            self.assertEqual(len(stored_files), 1)

            payload = json.loads(stored_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["meta"]["display-name"], "第一条用户消息已经超过二十个字符限制了并...")
            self.assertEqual(
                [message["role"] for message in payload["messages"]],
                ["system", "user", "user"],
            )
            self.assertTrue(all("timestamp" in message["meta"] for message in payload["messages"]))

    def test_append_message_requires_started_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(
                system_instruction="system",
                user_instruction="memory",
                originals_dir=Path(temp_dir),
            )

            with self.assertRaisesRegex(RuntimeError, "尚未开始"):
                store.append_message({"role": "assistant", "content": "hello"})

    def test_has_persisted_conversation_reflects_json_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(
                system_instruction="system",
                user_instruction="memory",
                originals_dir=Path(temp_dir),
            )

            self.assertFalse(store.has_persisted_conversation())
            store.start_with_first_user_message(user_content="hello")
            self.assertTrue(store.has_persisted_conversation())

    def test_truncate_display_name_limits_to_twenty_characters_and_appends_ellipsis(self) -> None:
        self.assertEqual(truncate_display_name("12345678901234567890"), "12345678901234567890")
        self.assertEqual(
            truncate_display_name("123456789012345678901"),
            "12345678901234567890...",
        )


if __name__ == "__main__":
    unittest.main()
