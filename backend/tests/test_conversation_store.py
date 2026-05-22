import json
import tempfile
import unittest
from pathlib import Path

from src.conversation_store import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def test_start_with_first_user_message_creates_json_with_basic_structure(self) -> None:
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
            self.assertIsInstance(payload["meta"], dict)
            self.assertNotIn("display-name", payload["meta"])
            self.assertEqual(
                [message["role"] for message in payload["messages"]],
                ["system", "user", "user"],
            )
            self.assertTrue(all("meta" not in message for message in payload["messages"]))

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

    def test_memory_manager_state_is_persisted_and_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            originals_dir = Path(temp_dir)
            store = ConversationStore(
                system_instruction="system",
                user_instruction="memory",
                originals_dir=originals_dir,
            )

            store.start_with_first_user_message(user_content="hello")
            store.update_memory_manager_state(
                summary_awaken_count=2,
                judge_awaken_count=3,
            )
            store.update_memory_manager_checkpoint_tokens(last_checkpoint_tokens=123)

            loaded_store = ConversationStore.load_from_conversation_file_name(
                conversation_file_name=store.conversation_file_name,
                originals_dir=originals_dir,
            )

            self.assertEqual(loaded_store.memory_manager_summary_awaken_count, 2)
            self.assertEqual(loaded_store.memory_manager_judge_awaken_count, 3)
            self.assertEqual(loaded_store.memory_manager_last_checkpoint_tokens, 123)

    def test_pause_state_is_persisted_and_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            originals_dir = Path(temp_dir)
            store = ConversationStore(
                system_instruction="system",
                user_instruction="memory",
                originals_dir=originals_dir,
            )

            store.start_with_first_user_message(user_content="hello")
            store.update_pause_state(pause_requested=True, paused=False)

            loaded_store = ConversationStore.load_from_conversation_file_name(
                conversation_file_name=store.conversation_file_name,
                originals_dir=originals_dir,
            )

            self.assertTrue(loaded_store.pause_requested)
            self.assertFalse(loaded_store.paused)

    def test_find_latest_conversation_file_name_returns_none_when_no_history_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(
                ConversationStore.find_latest_conversation_file_name(originals_dir=Path(temp_dir))
            )

    def test_find_latest_conversation_file_name_uses_conversation_file_name_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            originals_dir = Path(temp_dir)
            older_id = "calm-river-20260429T120000000000Z.json"
            newer_id = "bold-sun-20260429T120100000000Z.json"
            (originals_dir / newer_id).write_text("not json", encoding="utf-8")
            (originals_dir / older_id).write_text("not json", encoding="utf-8")

            self.assertEqual(
                ConversationStore.find_latest_conversation_file_name(originals_dir=originals_dir),
                newer_id,
            )

    def test_find_latest_conversation_file_name_ignores_files_without_parseable_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            originals_dir = Path(temp_dir)
            latest_id = "calm-river-20260429T120000000000Z.json"
            (originals_dir / "invalid-latest.json").write_text("not json", encoding="utf-8")
            (originals_dir / "missing-timestamp.json").write_text("not json", encoding="utf-8")
            (originals_dir / latest_id).write_text("not json", encoding="utf-8")

            self.assertEqual(
                ConversationStore.find_latest_conversation_file_name(originals_dir=originals_dir),
                latest_id,
            )


if __name__ == "__main__":
    unittest.main()
