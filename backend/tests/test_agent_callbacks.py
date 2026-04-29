import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.conversation_store import ConversationStore
from src.core.agent import Agent
from src.core.agent_turn import ToolSpec, execute_tool_calls
from src.core.memory_manager import MemoryManagerResult
from src.core.model_config import ModelConfig
from src.tools.reset_context import RESET_CONTEXT_AUTO_REMINDER


async def _echo_handler(*, arguments: dict[str, object]) -> dict[str, object]:
    return {"echoed": arguments["value"]}


async def _raw_text_handler(*, arguments: dict[str, object]) -> str:
    return str(arguments["value"])


async def _boom_handler(*, arguments: dict[str, object]) -> object:
    _ = arguments
    raise RuntimeError("boom")


class _StaticMemoryManagerRunner:
    def __init__(self, *, requested_reset_context: bool) -> None:
        self.requested_reset_context = requested_reset_context
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> MemoryManagerResult:
        self.calls.append(kwargs)
        return MemoryManagerResult(requested_reset_context=self.requested_reset_context)


class AgentCallbackTests(unittest.IsolatedAsyncioTestCase):

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
            handler=_echo_handler,
        )

    async def test_enqueue_user_message_uses_frontend_msg_id(self) -> None:
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

                drained = agent._safe_drain_user_message_queue()
                stored_files = list(Path(temp_dir).glob("*.json"))

                self.assertEqual(len(stored_files), 1)

        self.assertEqual(enqueued_ids, ["frontend-1"])
        self.assertEqual(committed_ids, ["frontend-1"])
        self.assertEqual(drained, 1)
        self.assertEqual(agent._messages[-1], {"role": "user", "content": "world"})

    async def test_execute_tool_calls_emits_tool_result(self) -> None:
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

        outcome = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={"echo": self._echo_tool()},
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

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

    async def test_execute_tool_calls_returns_tool_error_for_unregistered_reset_context(self) -> None:
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

        outcome = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={},
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertEqual(len(outcome.tool_messages), 1)
        self.assertEqual(len(tool_results), 1)
        parsed = json.loads(outcome.tool_messages[0]["content"])
        self.assertEqual(parsed["tool"], "reset_context")
        self.assertEqual(parsed["stage"], "run")
        self.assertIn("未注册的工具", parsed["error"])

    async def test_execute_tool_calls_preserves_raw_text_result(self) -> None:
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

        outcome = await execute_tool_calls(
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
                    handler=_raw_text_handler,
                ),
            },
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertEqual(tool_results[0]["result_json_str"], "keep this")
        self.assertEqual(outcome.tool_messages[0]["content"], "keep this")

    async def test_execute_tool_calls_returns_tool_error_when_arguments_invalid_json(self) -> None:
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

        outcome = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={"echo": self._echo_tool()},
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertEqual(len(outcome.tool_messages), 1)
        parsed = json.loads(outcome.tool_messages[0]["content"])
        self.assertEqual(parsed["tool"], "echo")
        self.assertEqual(parsed["stage"], "parse")
        self.assertIn("JSONDecodeError", parsed["error"])
        self.assertEqual(len(tool_results), 1)

    async def test_execute_tool_calls_returns_tool_error_when_handler_raises(self) -> None:
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

        outcome = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools_by_name={
                "boom": ToolSpec(
                    name="boom",
                    description="总是抛异常",
                    parameters_json_schema={"type": "object", "properties": {}},
                    handler=_boom_handler,
                )
            },
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertEqual(len(outcome.tool_messages), 1)
        parsed = json.loads(outcome.tool_messages[0]["content"])
        self.assertEqual(parsed["tool"], "boom")
        self.assertEqual(parsed["stage"], "run")
        self.assertIn("RuntimeError", parsed["error"])
        self.assertIn("boom", parsed["error"])
        self.assertEqual(len(tool_results), 1)

    async def test_run_passes_on_tool_result_through_agent(self) -> None:
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
                    new=mock.AsyncMock(side_effect=[ai_msg_with_tool_call, final_ai_msg]),
                ):
                    result = await agent.run()

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

    async def test_append_runtime_message_requires_persisted_conversation(self) -> None:
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

    async def test_run_requires_first_user_message(self) -> None:
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

                with mock.patch.object(Agent, "_safe_stream", new=mock.AsyncMock(side_effect=AssertionError("不应调用 _safe_stream"))):
                    with self.assertRaisesRegex(RuntimeError, "尚未开始"):
                        await agent.run()

    async def test_agent_rejects_duplicate_tool_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "重复"):
            Agent(
                name="demo",
                model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                system_instruction="system",
                user_instruction="hello",
                tools=[self._echo_tool(), self._echo_tool()],
            )

    async def test_memory_manager_magic_word_switches_conversation(self) -> None:
        reset_events: list[dict[str, str]] = []
        runner = _StaticMemoryManagerRunner(requested_reset_context=True)

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
                    tools=[self._echo_tool()],
                    on_reset_context=lambda *, conversation_id, display_name: reset_events.append(
                        {"conversation_id": conversation_id, "display_name": display_name}
                    ),
                    memory_manager_runner=runner,
                    memory_manager_turn_interval=1,
                    loaded_main_memory_content="memory before reset",
                )
                agent.new_conversation()

                ai_msg_with_tool_call = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_echo_1",
                            "type": "function",
                            "function": {"name": "echo", "arguments": "{\"value\": 1}"},
                        }
                    ],
                }
                final_ai_msg = {"role": "assistant", "content": "done after reset"}

                with mock.patch.object(
                    Agent,
                    "_safe_stream",
                    new=mock.AsyncMock(side_effect=[ai_msg_with_tool_call, final_ai_msg]),
                ), mock.patch(
                    "src.prompts.builder.build_system_level_instruction_zh",
                    return_value="system-2",
                ), mock.patch(
                    "src.prompts.builder.build_user_level_instruction_zh",
                    return_value="user-2",
                ), mock.patch(
                    "src.prompts.builder.read_main_memory",
                    return_value="memory after reset",
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    result = await agent.run()

                stored_files = list(Path(temp_dir).glob("*.json"))
                self.assertEqual(len(stored_files), 2)

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(len(reset_events), 1)
        self.assertEqual(reset_events[0]["display_name"], "hello")
        self.assertEqual(agent._messages[0], {"role": "system", "content": "system-2"})
        self.assertEqual(agent._messages[1], {"role": "user", "content": "user-2"})
        self.assertEqual(agent._messages[2], {"role": "user", "content": RESET_CONTEXT_AUTO_REMINDER})
        self.assertEqual(agent._worker_turns_since_memory_manager, 0)
        self.assertEqual(agent._memory_manager_awaken_count, 0)
        self.assertEqual(agent._loaded_main_memory_content, "memory after reset")

    async def test_memory_manager_does_not_awaken_before_tool_turn_interval(self) -> None:
        runner = _StaticMemoryManagerRunner(requested_reset_context=False)

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
                    user_instruction="user",
                    tools=[self._echo_tool()],
                    memory_manager_runner=runner,
                    memory_manager_turn_interval=2,
                )
                agent.new_conversation()

                ai_msg_with_tool_call = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_echo_1",
                            "type": "function",
                            "function": {"name": "echo", "arguments": "{\"value\": 1}"},
                        }
                    ],
                }
                final_ai_msg = {"role": "assistant", "content": "done"}

                with mock.patch.object(
                    Agent,
                    "_safe_stream",
                    new=mock.AsyncMock(side_effect=[ai_msg_with_tool_call, final_ai_msg]),
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    result = await agent.run()

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(runner.calls, [])
        self.assertEqual(agent._worker_turns_since_memory_manager, 1)
        self.assertEqual(agent._memory_manager_awaken_count, 0)

    async def test_memory_manager_awakes_when_tool_turn_interval_is_reached(self) -> None:
        runner = _StaticMemoryManagerRunner(requested_reset_context=False)

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
                    user_instruction="user",
                    tools=[self._echo_tool()],
                    memory_manager_runner=runner,
                    memory_manager_turn_interval=2,
                    loaded_main_memory_content="main memory snapshot",
                )
                agent.new_conversation()

                ai_msgs_with_tool_call = [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_echo_{value}",
                                "type": "function",
                                "function": {"name": "echo", "arguments": f"{{\"value\": {value}}}"},
                            }
                        ],
                    }
                    for value in (1, 2)
                ]
                final_ai_msg = {"role": "assistant", "content": "done"}

                with mock.patch.object(
                    Agent,
                    "_safe_stream",
                    new=mock.AsyncMock(side_effect=[*ai_msgs_with_tool_call, final_ai_msg]),
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    result = await agent.run()

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(runner.calls), 1)
        self.assertTrue(runner.calls[0]["is_first_time_awaken"])
        self.assertEqual(runner.calls[0]["loaded_main_memory_content"], "main memory snapshot")
        self.assertEqual(agent._worker_turns_since_memory_manager, 0)
        self.assertEqual(agent._memory_manager_awaken_count, 1)


if __name__ == "__main__":
    unittest.main()
