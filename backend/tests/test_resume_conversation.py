import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.conversation_store import ConversationStore
from src.core.agent import Agent
from src.core.model_config import ModelConfig

OLD_INIT_MESSAGES = [
    {"role": "system", "content": "system-old"},
    {"role": "user", "content": "user-old"},
]
NEW_INIT_MESSAGES = [
    {"role": "system", "content": "system-new"},
    {"role": "user", "content": "user-new"},
]


class StartConversationTests(unittest.TestCase):
    def test_start_conversation_restores_latest_messages_and_reuses_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            originals_dir = Path(temp_dir)
            store = ConversationStore(init_messages=OLD_INIT_MESSAGES, originals_dir=originals_dir)
            store.start_with_first_user_message(user_content="hello")
            store.append_message({"role": "assistant", "content": "hi"})
            conversation_file_name = store.conversation_file_name
            switch_events: list[list[dict]] = []

            stored_files = list(originals_dir.glob("*.json"))
            self.assertEqual(len(stored_files), 1)
            file_path = stored_files[0]
            self.assertEqual(file_path.name, conversation_file_name)

            agent = Agent(
                name="demo",
                model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                init_messages=NEW_INIT_MESSAGES,
                tools=[],
                on_switch_conversation=lambda *, visible_messages: switch_events.append(visible_messages),
            )

            with mock.patch("src.conversation_store.ORIGINALS_DIR", originals_dir):
                agent.start_conversation()

            self.assertEqual(agent._init_messages, OLD_INIT_MESSAGES)
            self.assertTrue(all("meta" not in m for m in agent._messages))
            self.assertEqual(agent._messages[0]["content"], "system-old")
            self.assertEqual(agent._messages[1]["content"], "user-old")
            self.assertEqual(
                switch_events,
                [[
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ]],
            )

            agent.enqueue_user_message(frontend_msg_id="frontend-1", user_message="next")
            agent._safe_drain_user_message_queue()

            stored_files_after = list(originals_dir.glob("*.json"))
            self.assertEqual(stored_files_after, [file_path])
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["messages"][-1]["role"], "user")
            self.assertEqual(payload["messages"][-1]["content"], "next")
            self.assertNotIn("meta", payload["messages"][-1])

    def test_start_conversation_rejects_path_like_latest_file_name(self) -> None:
        agent = Agent(
            name="demo",
            model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
            init_messages=[{"role": "user", "content": "user"}],
            tools=[],
        )
        with (
            mock.patch(
                "src.conversation_store.ConversationStore.find_latest_conversation_file_name",
                return_value="../evil.json",
            ),
            self.assertRaisesRegex(ValueError, "不允许包含路径"),
        ):
            agent.start_conversation()

    def test_start_conversation_restores_memory_manager_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            originals_dir = Path(temp_dir)
            store = ConversationStore(init_messages=OLD_INIT_MESSAGES, originals_dir=originals_dir)
            store.start_with_first_user_message(user_content="hello")
            store.update_memory_manager_state(
                summarizer_awaken_count=2,
                decider_awaken_count=3,
            )
            store.update_memory_manager_last_triggered_threshold(last_triggered_threshold=33)

            agent = Agent(
                name="demo",
                model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                init_messages=NEW_INIT_MESSAGES,
                tools=[],
            )

            with mock.patch("src.conversation_store.ORIGINALS_DIR", originals_dir):
                agent.start_conversation()

        self.assertEqual(agent._summarizer_awaken_count, 2)
        self.assertEqual(agent._decider_awaken_count, 3)
        self.assertEqual(agent._conversation_store.memory_manager_last_triggered_threshold, 33)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
