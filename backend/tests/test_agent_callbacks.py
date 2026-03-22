import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.agent import Agent
from src.conversation_store import ConversationStore
from src.core.agent_turn import ContinueLoopDirective, ToolSpec, execute_tool_calls
from src.core.model_config import ModelConfig


class AgentCallbackTests(unittest.TestCase):

    @staticmethod
    def _echo_tool() -> ToolSpec:
        return ToolSpec(
            name="echo",
            description="回显输入值",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "value": {"type": "integer"},
                },
                "required": ["value"],
            },
            handler=lambda *, arguments: {"echoed": arguments["value"]},
        )

    def test_enqueue_user_message_uses_frontend_msg_id(self) -> None:
        enqueued_ids: list[str] = []
        committed_ids: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch(
                "src.core.agent.ConversationStore",
                side_effect=lambda *, system_instruction, user_instruction: ConversationStore(
                    system_instruction=system_instruction,
                    user_instruction=user_instruction,
                    originals_dir=Path(temp_dir),
                ),
            ):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="hello",
                    tools=[],
                    on_user_msg_enqueued=lambda *, frontend_msg_id: enqueued_ids.append(frontend_msg_id),
                    on_queued_user_msg_committed=lambda *, frontend_msg_id: committed_ids.append(frontend_msg_id),
                )

                agent.new_conversation()
                self.assertEqual(list(Path(temp_dir).glob("*.json")), [])

                agent.enqueue_user_message(frontend_msg_id="frontend-1", user_message="world")
                self.assertEqual(list(Path(temp_dir).glob("*.json")), [])

                drained = agent._safe_drain_user_message_queue(agent._user_msg_queue, agent._messages)
                stored_files = list(Path(temp_dir).glob("*.json"))

                self.assertEqual(len(stored_files), 1)

        self.assertEqual(enqueued_ids, ["frontend-1"])
        self.assertEqual(committed_ids, ["frontend-1"])
        self.assertEqual(drained, 1)
        self.assertEqual(agent._messages[-1], {"role": "user", "content": "world"})

    def test_execute_tool_calls_emits_tool_result(self) -> None:
        tool_results: list[dict[str, object]] = []
        ai_msg_dict = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": "{\"value\": 1}",
                    },
                }
            ],
        }

        outcome = execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={"echo": self._echo_tool()},
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertIsInstance(outcome.directive, ContinueLoopDirective)
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(
            tool_results[0],
            {
                "tool_call_id": "call_1",
                "result_json_str": "{\"echoed\": 1}",
            },
        )
        self.assertEqual(
            outcome.tool_messages,
            [
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "{\"echoed\": 1}",
                }
            ],
        )

    def test_execute_tool_calls_always_returns_continue_loop_directive(self) -> None:
        tool_results: list[dict[str, object]] = []
        ai_msg_dict = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "raw_text",
                        "arguments": "{\"value\": \"keep this\"}",
                    },
                }
            ],
        }

        outcome = execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={
                "raw_text": ToolSpec(
                    name="raw_text",
                    description="返回纯文本",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                        },
                        "required": ["value"],
                    },
                    handler=lambda *, arguments: arguments["value"],
                ),
            },
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertIsInstance(outcome.directive, ContinueLoopDirective)
        self.assertEqual(tool_results[0]["result_json_str"], "keep this")
        self.assertEqual(outcome.tool_messages[0]["content"], "keep this")

    def test_run_passes_on_tool_result_through_agent(self) -> None:
        tool_results: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch(
                "src.core.agent.ConversationStore",
                side_effect=lambda *, system_instruction, user_instruction: ConversationStore(
                    system_instruction=system_instruction,
                    user_instruction=user_instruction,
                    originals_dir=Path(temp_dir),
                ),
            ):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="hello",
                    tools=[self._echo_tool()],
                    on_tool_result=lambda **kwargs: tool_results.append(kwargs),
                )
                agent.new_conversation()
                agent.enqueue_user_message(
                    frontend_msg_id="frontend-1",
                    user_message="第一条用户消息已经超过二十个字符限制了并且后面还有内容",
                )

                ai_msg_with_tool_call = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": json.dumps({"value": 7}, ensure_ascii=False),
                            },
                        }
                    ],
                }
                final_ai_msg = {
                    "role": "assistant",
                    "content": "done",
                }

                with mock.patch.object(
                    Agent,
                    "_safe_stream",
                    side_effect=[ai_msg_with_tool_call, final_ai_msg],
                ):
                    result = agent.run()

                stored_files = list(Path(temp_dir).glob("*.json"))
                self.assertEqual(len(stored_files), 1)
                stored_payload = json.loads(stored_files[0].read_text(encoding="utf-8"))

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0]["tool_call_id"], "call_1")
        self.assertEqual(tool_results[0]["result_json_str"], "{\"echoed\": 7}")
        self.assertEqual(
            agent._messages[-1],
            {
                "role": "assistant",
                "content": "done",
            },
        )
        self.assertEqual(stored_payload["meta"]["display-name"], "第一条用户消息已经超过二十个字符限制了并...")
        self.assertEqual(
            [message["role"] for message in stored_payload["messages"]],
            ["system", "user", "user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(stored_payload["messages"][4]["content"], "{\"echoed\": 7}")
        self.assertTrue(all("timestamp" in message["meta"] for message in stored_payload["messages"]))

    def test_append_runtime_message_requires_persisted_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch(
                "src.core.agent.ConversationStore",
                side_effect=lambda *, system_instruction, user_instruction: ConversationStore(
                    system_instruction=system_instruction,
                    user_instruction=user_instruction,
                    originals_dir=Path(temp_dir),
                ),
            ):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="hello",
                    tools=[],
                )
                agent.new_conversation()

                with self.assertRaisesRegex(RuntimeError, "尚未开始"):
                    agent._append_runtime_message({"role": "assistant", "content": "hi"})

    def test_agent_rejects_duplicate_tool_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "重复"):
            Agent(
                name="demo",
                model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                system_instruction="system",
                user_instruction="hello",
                tools=[self._echo_tool(), self._echo_tool()],
            )


if __name__ == "__main__":
    unittest.main()
