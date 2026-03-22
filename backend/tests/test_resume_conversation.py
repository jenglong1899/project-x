import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.conversation_store import ConversationStore
from src.core.agent import Agent
from src.core.model_config import ModelConfig


class ResumeConversationTests(unittest.TestCase):
    def test_resume_conversation_restores_messages_and_reuses_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            originals_dir = Path(temp_dir)
            store = ConversationStore(
                system_instruction="system-old",
                user_instruction="user-old",
                originals_dir=originals_dir,
            )
            store.start_with_first_user_message(user_content="hello")
            store.append_message({"role": "assistant", "content": "hi"})
            conversation_id = store.conversation_id

            stored_files = list(originals_dir.glob("*.json"))
            self.assertEqual(len(stored_files), 1)
            file_path = stored_files[0]
            self.assertEqual(file_path.name, conversation_id)

            agent = Agent(
                name="demo",
                model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                system_instruction="system-new",
                user_instruction="user-new",
                tools=[],
            )

            with mock.patch("src.conversation_store.ORIGINALS_DIR", originals_dir):
                agent.resume_conversation(conversation_id=conversation_id)

            self.assertEqual(agent._system_instruction, "system-old")
            self.assertEqual(agent._user_instruction, "user-old")
            self.assertTrue(all("meta" not in m for m in agent._messages))
            self.assertEqual(agent._messages[0]["content"], "system-old")
            self.assertEqual(agent._messages[1]["content"], "user-old")

            agent.enqueue_user_message(frontend_msg_id="frontend-1", user_message="next")
            drained = agent._safe_drain_user_message_queue(agent._user_msg_queue, agent._messages)
            self.assertEqual(drained, 1)

            stored_files_after = list(originals_dir.glob("*.json"))
            self.assertEqual(stored_files_after, [file_path])
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["messages"][-1]["role"], "user")
            self.assertEqual(payload["messages"][-1]["content"], "next")
            self.assertIn("timestamp", payload["messages"][-1]["meta"])

    def test_resume_conversation_rejects_path_like_id(self) -> None:
        agent = Agent(
            name="demo",
            model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
            system_instruction="system",
            user_instruction="user",
            tools=[],
        )
        with self.assertRaisesRegex(ValueError, "不允许包含路径"):
            agent.resume_conversation(conversation_id="../evil")


if __name__ == "__main__":
    unittest.main()
