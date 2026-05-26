import json
import asyncio
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from src.conversation_store import ConversationStore
from src.commons import WAKE_MEMORY_MANAGER_FLAG
from src.core.agent import Agent
from src.core.agent_turn import Tool, execute_tool_calls
from src.core.model_config import ModelConfig


class _FakeTokenCounter:
    def __init__(self, *, context_window: int = 100, token_per_message: int = 10) -> None:
        self._context_window = context_window
        self._token_per_message = token_per_message

    def context_window(self, model: str) -> int:  # noqa: ARG002
        return self._context_window

    def count_messages_tokens(self, model: str, messages: list[dict[str, object]]) -> tuple[int, bool]:  # noqa: ARG002
        return len(messages) * self._token_per_message, True


async def _echo_handler(*, arguments: dict[str, object]) -> dict[str, object]:
    return {"echoed": arguments["value"]}


async def _raw_text_handler(*, arguments: dict[str, object]) -> str:
    return str(arguments["value"])


async def _boom_handler(*, arguments: dict[str, object]) -> object:
    _ = arguments
    raise RuntimeError("boom")


class _StaticSummaryRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        return None


class _StaticJudgeRunner:
    def __init__(self, *, should_reset_context: bool) -> None:
        self.should_reset_context = should_reset_context
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> bool:
        self.calls.append(kwargs)
        return self.should_reset_context


class _BlockingSummaryRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.started = asyncio.Event()
        self.allow_finish = asyncio.Event()

    async def run(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        self.started.set()
        await self.allow_finish.wait()
        return None


@contextmanager
def _patch_agent_conversation_store_without_history(temp_dir: str) -> Iterator[None]:
    with mock.patch("src.core.agent.ConversationStore") as store_cls:
        store_cls.find_latest_conversation_file_name.return_value = None
        store_cls.side_effect = lambda *, system_instruction, user_instruction: ConversationStore(
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            originals_dir=Path(temp_dir),
        )
        yield


class AgentCallbackTests(unittest.IsolatedAsyncioTestCase):

    @staticmethod
    def _echo_tool() -> Tool:
        return Tool(
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
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="hello",
                    tools=[],
                    on_user_msg_enqueued=lambda *, frontend_msg_id: enqueued_ids.append(frontend_msg_id),
                    on_queued_user_msg_committed=lambda *, frontend_msg_id: committed_ids.append(frontend_msg_id),
                )

                agent.start_conversation()
                self.assertEqual(list(Path(temp_dir).glob("*.json")), [])

                agent.enqueue_user_message(frontend_msg_id="frontend-1", user_message="world")
                self.assertEqual(list(Path(temp_dir).glob("*.json")), [])

                agent._safe_drain_user_message_queue()
                stored_files = list(Path(temp_dir).glob("*.json"))

                self.assertEqual(len(stored_files), 1)

        self.assertEqual(enqueued_ids, ["frontend-1"])
        self.assertEqual(committed_ids, ["frontend-1"])
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

        tool_messages = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools=[self._echo_tool()],
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
            tool_messages,
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

        tool_messages = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools=[],
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(len(tool_results), 1)
        parsed = json.loads(tool_messages[0]["content"])
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

        tool_messages = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools=[
                Tool(
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
            ],
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertEqual(tool_results[0]["result_json_str"], "keep this")
        self.assertEqual(tool_messages[0]["content"], "keep this")

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

        tool_messages = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools=[self._echo_tool()],
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertEqual(len(tool_messages), 1)
        parsed = json.loads(tool_messages[0]["content"])
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

        tool_messages = await execute_tool_calls(
            ai_msg_dict=ai_msg_dict,
            tools=[
                Tool(
                    name="boom",
                    description="总是抛异常",
                    parameters_json_schema={"type": "object", "properties": {}},
                    handler=_boom_handler,
                )
            ],
            on_tool_result=lambda **kwargs: tool_results.append(kwargs),
        )

        self.assertEqual(len(tool_messages), 1)
        parsed = json.loads(tool_messages[0]["content"])
        self.assertEqual(parsed["tool"], "boom")
        self.assertEqual(parsed["stage"], "run")
        self.assertIn("RuntimeError", parsed["error"])
        self.assertIn("boom", parsed["error"])
        self.assertEqual(len(tool_results), 1)

    async def test_run_passes_on_tool_result_through_agent(self) -> None:
        tool_results: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="hello",
                    tools=[self._echo_tool()],
                    on_tool_result=lambda **kwargs: tool_results.append(kwargs),
                )
                agent.start_conversation()
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
        self.assertIsInstance(stored_payload["meta"], dict)
        self.assertNotIn("display-name", stored_payload["meta"])
        self.assertEqual(
            [message["role"] for message in stored_payload["messages"]],
            ["system", "user", "user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(stored_payload["messages"][4]["content"], "{\"echoed\": 7}")
        self.assertTrue(all("meta" not in message for message in stored_payload["messages"]))

    async def test_append_runtime_message_requires_persisted_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="hello",
                    tools=[],
                )
                agent.start_conversation()

                with self.assertRaisesRegex(RuntimeError, "尚未开始"):
                    agent._append_runtime_message({"role": "assistant", "content": "hi"})

    async def test_run_requires_first_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="hello",
                    tools=[],
                )
                agent.start_conversation()

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
        switch_events: list[list[dict[str, object]]] = []
        summary_runner = _StaticSummaryRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system-1",
                    user_instruction="user-1",
                    tools=[self._echo_tool()],
                    on_switch_conversation=lambda *, visible_messages: switch_events.append(visible_messages),
                    token_counter=_FakeTokenCounter(),
                )
                agent._memory_manager_summary_runner = summary_runner
                agent._memory_manager_judge_runner = judge_runner
                agent.start_conversation()

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
                    "src.core.init_prompts.build_system_level_instruction_zh",
                    return_value="system-2",
                ), mock.patch(
                    "src.core.init_prompts.build_user_level_instruction_zh",
                    return_value="user-2",
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    agent._safe_drain_user_message_queue()
                    agent._require_conversation_store().update_memory_manager_checkpoint_tokens(last_checkpoint_tokens=1)
                    result = await agent.run()

                stored_files = list(Path(temp_dir).glob("*.json"))
                self.assertEqual(len(stored_files), 2)

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(summary_runner.calls), 1)
        self.assertEqual(len(judge_runner.calls), 1)
        self.assertEqual(
            switch_events[-1],
            [
                {"role": "assistant", "content": None, "tool_calls": mock.ANY},
                {"role": "tool", "tool_call_id": "call_echo_1", "content": "{\"echoed\": 1}"},
                {"role": "user", "content": WAKE_MEMORY_MANAGER_FLAG},
            ],
        )
        self.assertEqual(agent._messages[0], {"role": "system", "content": "system-2"})
        self.assertEqual(agent._messages[1], {"role": "user", "content": "user-2"})
        self.assertEqual(agent._memory_manager_summary_awaken_count, 0)
        self.assertEqual(agent._memory_manager_judge_awaken_count, 0)

    async def test_memory_manager_does_not_awaken_below_context_growth_threshold(self) -> None:
        summary_runner = _StaticSummaryRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="user",
                    tools=[self._echo_tool()],
                    token_counter=_FakeTokenCounter(context_window=100, token_per_message=1),
                )
                agent._memory_manager_summary_runner = summary_runner
                agent._memory_manager_judge_runner = judge_runner
                agent.start_conversation()

                agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                agent._safe_drain_user_message_queue()
                store = agent._require_conversation_store()
                store.update_memory_manager_checkpoint_tokens(last_checkpoint_tokens=3)

                await agent._maybe_wake_memory_manager()

        self.assertEqual(summary_runner.calls, [])
        self.assertEqual(judge_runner.calls, [])
        self.assertEqual(agent._memory_manager_summary_awaken_count, 0)
        self.assertEqual(agent._memory_manager_judge_awaken_count, 0)

    async def test_memory_manager_awakes_when_context_growth_threshold_is_reached(self) -> None:
        summary_runner = _StaticSummaryRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system",
                    user_instruction="user",
                    tools=[self._echo_tool()],
                    token_counter=_FakeTokenCounter(context_window=100, token_per_message=10),
                )
                agent._memory_manager_summary_runner = summary_runner
                agent._memory_manager_judge_runner = judge_runner
                agent.start_conversation()

                ai_msgs_with_tool_call = [
                    {
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
                ]
                final_ai_msg = {"role": "assistant", "content": "done"}

                with mock.patch.object(
                    Agent,
                    "_safe_stream",
                    new=mock.AsyncMock(side_effect=[*ai_msgs_with_tool_call, final_ai_msg]),
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    agent._safe_drain_user_message_queue()
                    agent._require_conversation_store().update_memory_manager_checkpoint_tokens(last_checkpoint_tokens=1)
                    result = await agent.run()

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(summary_runner.calls), 1)
        self.assertTrue(summary_runner.calls[0]["is_first_time_awaken"])
        self.assertEqual(len(judge_runner.calls), 1)
        self.assertEqual(agent._memory_manager_summary_awaken_count, 1)
        self.assertEqual(agent._memory_manager_judge_awaken_count, 1)

    async def test_judge_reset_waits_for_summary_before_switching_conversation(self) -> None:
        switch_events: list[list[dict[str, object]]] = []
        summary_runner = _BlockingSummaryRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system-1",
                    user_instruction="user-1",
                    tools=[self._echo_tool()],
                    on_switch_conversation=lambda *, visible_messages: switch_events.append(visible_messages),
                    token_counter=_FakeTokenCounter(),
                )
                agent._memory_manager_summary_runner = summary_runner
                agent._memory_manager_judge_runner = judge_runner
                agent.start_conversation()

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
                ), mock.patch(
                    "src.core.init_prompts.build_system_level_instruction_zh",
                    return_value="system-2",
                ), mock.patch(
                    "src.core.init_prompts.build_user_level_instruction_zh",
                    return_value="user-2",
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    agent._safe_drain_user_message_queue()
                    agent._require_conversation_store().update_memory_manager_checkpoint_tokens(last_checkpoint_tokens=1)
                    run_task = asyncio.create_task(agent.run())
                    await summary_runner.started.wait()
                    self.assertEqual(switch_events, [[]])
                    summary_runner.allow_finish.set()
                    result = await run_task

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(summary_runner.calls), 1)
        self.assertEqual(len(judge_runner.calls), 1)
        self.assertEqual(len(switch_events), 2)

    async def test_summary_does_not_reenter_while_in_flight(self) -> None:
        summary_runner = _BlockingSummaryRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    system_instruction="system-1",
                    user_instruction="user-1",
                    tools=[self._echo_tool()],
                    token_counter=_FakeTokenCounter(),
                )
                agent._memory_manager_summary_runner = summary_runner
                agent._memory_manager_judge_runner = judge_runner
                agent.start_conversation()

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
                    agent._safe_drain_user_message_queue()
                    agent._require_conversation_store().update_memory_manager_checkpoint_tokens(last_checkpoint_tokens=1)
                    result = await agent.run()

                summary_runner.allow_finish.set()
                await agent._memory_manager_summary_task

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(summary_runner.calls), 1)
        self.assertEqual(len(judge_runner.calls), 2)
        self.assertEqual(agent._memory_manager_summary_awaken_count, 1)
        self.assertEqual(agent._memory_manager_judge_awaken_count, 2)
        self.assertEqual(
            sum(1 for m in agent._messages if m.get("role") == "user" and m.get("content") == WAKE_MEMORY_MANAGER_FLAG),
            1,
        )


if __name__ == "__main__":
    unittest.main()
