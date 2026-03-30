import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.agent import Agent
from src.conversation_store import ConversationStore
from src.core.agent_turn import ContinueLoopDirective, ResetContextDirective, ToolSpec, execute_tool_calls
from src.core.model_config import ModelConfig
from src.tools.reset_context import (
    RESET_CONTEXT_AUTO_REMINDER,
    RESET_CONTEXT_FIRST_CALL_HINT,
    RESET_CONTEXT_TOOL,
)


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

    def test_execute_tool_calls_returns_reset_context_directive(self) -> None:
        tool_results: list[dict[str, object]] = []
        ai_msg_dict = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_reset",
                    "type": "function",
                    "function": {
                        "name": "reset_context",
                        "arguments": "",
                    },
                }
            ],
        }

        outcome = execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={},
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertIsInstance(outcome.directive, ResetContextDirective)
        self.assertEqual(outcome.tool_messages, [])
        self.assertEqual(tool_results, [])

    def test_execute_tool_calls_returns_tool_error_when_reset_context_is_concurrent(self) -> None:
        tool_results: list[dict[str, object]] = []
        ai_msg_dict = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_reset",
                    "type": "function",
                    "function": {
                        "name": "reset_context",
                        "arguments": "",
                    },
                },
                {
                    "id": "call_echo",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": "{\"value\": 1}",
                    },
                },
            ],
        }

        outcome = execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={},
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertIsInstance(outcome.directive, ContinueLoopDirective)
        self.assertEqual(len(outcome.tool_messages), 2)
        self.assertEqual(len(tool_results), 2)

        for tool_result in tool_results:
            parsed = json.loads(str(tool_result["result_json_str"]))
            self.assertIn("reset_context 不能和其他工具并发调用", parsed["error"])

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

    def test_execute_tool_calls_returns_tool_error_when_arguments_invalid_json(self) -> None:
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
                        "arguments": "{\"value\": }",
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
        self.assertEqual(len(outcome.tool_messages), 1)
        parsed = json.loads(outcome.tool_messages[0]["content"])
        self.assertEqual(parsed["tool"], "echo")
        self.assertEqual(parsed["stage"], "parse")
        self.assertIn("JSONDecodeError", parsed["error"])
        self.assertEqual(len(tool_results), 1)

    def test_execute_tool_calls_returns_tool_error_when_handler_raises(self) -> None:
        tool_results: list[dict[str, object]] = []
        ai_msg_dict = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "boom",
                        "arguments": "{}",
                    },
                }
            ],
        }

        outcome = execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={
                "boom": ToolSpec(
                    name="boom",
                    description="总是抛异常",
                    parameters_json_schema={"type": "object", "properties": {}},
                    handler=lambda *, arguments: (_ for _ in ()).throw(RuntimeError("boom")),
                )
            },
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertIsInstance(outcome.directive, ContinueLoopDirective)
        self.assertEqual(len(outcome.tool_messages), 1)
        parsed = json.loads(outcome.tool_messages[0]["content"])
        self.assertEqual(parsed["tool"], "boom")
        self.assertEqual(parsed["stage"], "run")
        self.assertIn("RuntimeError", parsed["error"])
        self.assertIn("boom", parsed["error"])
        self.assertEqual(len(tool_results), 1)

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

    def test_run_requires_first_user_message(self) -> None:
        """
        new_conversation() 后如果没有任何待处理的 user message，就不应进入模型生成路径。
        """
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

                with mock.patch.object(Agent, "_safe_stream", side_effect=AssertionError("不应调用 _safe_stream")):
                    with self.assertRaisesRegex(RuntimeError, "尚未开始"):
                        agent.run()

    def test_agent_rejects_duplicate_tool_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "重复"):
            Agent(
                name="demo",
                model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                system_instruction="system",
                user_instruction="hello",
                tools=[self._echo_tool(), self._echo_tool()],
            )

    def test_agent_reset_context_first_call_only_returns_hint(self) -> None:
        """
        第一次调用 reset_context：
        - 不切会话
        - 只追加一条 tool message（hint）
        """
        tool_results: list[dict[str, object]] = []
        reset_events: list[dict[str, str]] = []
        stored_payload: dict[str, object]

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
                    tools=[RESET_CONTEXT_TOOL],
                    on_tool_result=lambda **kwargs: tool_results.append(kwargs),
                    on_reset_context=lambda *, conversation_id, display_name: reset_events.append(
                        {"conversation_id": conversation_id, "display_name": display_name}
                    ),
                )
                agent.new_conversation()
                agent.enqueue_user_message(frontend_msg_id="u1", user_message="hi")

                ai_msg_with_reset_context = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_reset_1",
                            "type": "function",
                            "function": {"name": "reset_context", "arguments": ""},
                        }
                    ],
                }
                final_ai_msg = {"role": "assistant", "content": "done"}

                with mock.patch.object(
                    Agent,
                    "_safe_stream",
                    side_effect=[ai_msg_with_reset_context, final_ai_msg],
                ):
                    result = agent.run()

                stored_files = list(Path(temp_dir).glob("*.json"))
                self.assertEqual(len(stored_files), 1)
                stored_payload = json.loads(stored_files[0].read_text(encoding="utf-8"))

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(reset_events, [])
        self.assertEqual(
            tool_results,
            [
                {
                    "tool_call_id": "call_reset_1",
                    "result_json_str": json.dumps({"hint": RESET_CONTEXT_FIRST_CALL_HINT}, ensure_ascii=False),
                }
            ],
        )
        stored_tool_messages = [m for m in stored_payload["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(stored_tool_messages), 1)
        parsed = json.loads(stored_tool_messages[0]["content"])
        self.assertEqual(parsed["hint"], RESET_CONTEXT_FIRST_CALL_HINT)

    def test_agent_reset_context_second_call_switches_conversation(self) -> None:
        """
        第二次调用 reset_context：
        - 切到新会话
        - 新会话第一条 user message 为 auto_reminder
        - 触发 on_reset_context 回调
        """
        reset_events: list[dict[str, str]] = []
        old_conversation_id = ""
        old_display_name = ""

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
                    system_instruction="system-1",
                    user_instruction="user-1",
                    tools=[RESET_CONTEXT_TOOL],
                    on_reset_context=lambda *, conversation_id, display_name: reset_events.append(
                        {"conversation_id": conversation_id, "display_name": display_name}
                    ),
                )
                agent.new_conversation()

                ai_msg_with_reset_context_1 = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_reset_1",
                            "type": "function",
                            "function": {"name": "reset_context", "arguments": ""},
                        }
                    ],
                }
                ai_msg_with_reset_context_2 = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_reset_2",
                            "type": "function",
                            "function": {"name": "reset_context", "arguments": ""},
                        }
                    ],
                }
                final_ai_msg_1 = {"role": "assistant", "content": "done-1"}
                final_ai_msg_2 = {"role": "assistant", "content": "done-2"}

                with mock.patch.object(
                    Agent,
                    "_safe_stream",
                    side_effect=[
                        ai_msg_with_reset_context_1,
                        final_ai_msg_1,
                        ai_msg_with_reset_context_2,
                        final_ai_msg_2,
                    ],
                ):
                    # 第一次调用只提示，不切会话
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    agent.run()

                    stored_files_after_first = list(Path(temp_dir).glob("*.json"))
                    self.assertEqual(len(stored_files_after_first), 1)
                    old_conversation_id = stored_files_after_first[0].name
                    old_payload = json.loads(stored_files_after_first[0].read_text(encoding="utf-8"))
                    old_display_name = old_payload["meta"]["display-name"]

                    # 第二次调用会切会话（需 mock builder，避免测试写 ~/.project-x）
                    with mock.patch(
                        "src.prompts.builder.build_system_level_instruction_zh",
                        return_value="system-2",
                    ), mock.patch(
                        "src.prompts.builder.build_user_level_instruction_zh",
                        return_value="user-2",
                    ):
                        agent.enqueue_user_message(frontend_msg_id="u2", user_message="trigger second")
                        agent.run()

                stored_files = list(Path(temp_dir).glob("*.json"))
                self.assertEqual(len(stored_files), 2)

        self.assertEqual(len(reset_events), 1)
        self.assertNotEqual(reset_events[0]["conversation_id"], old_conversation_id)
        self.assertEqual(reset_events[0]["display_name"], old_display_name)
        self.assertEqual(agent._messages[0], {"role": "system", "content": "system-2"})
        self.assertEqual(agent._messages[1], {"role": "user", "content": "user-2"})
        self.assertEqual(agent._messages[2], {"role": "user", "content": RESET_CONTEXT_AUTO_REMINDER})


if __name__ == "__main__":
    unittest.main()
