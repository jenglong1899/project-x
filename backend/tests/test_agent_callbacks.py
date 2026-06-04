import json
import asyncio
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from src.conversation_store import ConversationStore
from src.commons import WAKE_MM_SUMMARY_FLAG
from src.core.agent import Agent
from src.core.agent_turn import Tool, TurnResult, TurnUsage, execute_tool_calls
from src.core.model_config import ModelConfig


async def _echo_handler(*, arguments: dict[str, object]) -> dict[str, object]:
    return {"echoed": arguments["value"]}


async def _raw_text_handler(*, arguments: dict[str, object]) -> str:
    return str(arguments["value"])


async def _boom_handler(*, arguments: dict[str, object]) -> object:
    _ = arguments
    raise RuntimeError("boom")


class _StaticSummarizerRunner:
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


class _SequenceJudgeRunner:
    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> bool:
        self.calls.append(kwargs)
        if not self._results:
            return False
        return self._results.pop(0)


class _BlockingSummarizerRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.started = asyncio.Event()
        self.allow_finish = asyncio.Event()

    async def run(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        self.started.set()
        await self.allow_finish.wait()
        return None


async def _wait_for_memory_manager_background_tasks(agent: Agent) -> None:
    settled_rounds = 0
    for _ in range(10):
        await asyncio.sleep(0)

        pending_tasks = [
            task
            for task in (
                agent._memory_manager_summarizer_task,
                agent._memory_manager_judge_task,
                agent._memory_manager_reset_task,
            )
            if task is not None and not task.done()
        ]
        if not pending_tasks:
            settled_rounds += 1
            if settled_rounds >= 2:
                return
            continue

        settled_rounds = 0

        for task in pending_tasks:
            await task


async def _wait_for_memory_manager_reset_task(agent: Agent) -> None:
    for _ in range(10):
        await asyncio.sleep(0)
        reset_task = agent._memory_manager_reset_task
        if reset_task is None:
            continue
        await reset_task
        await asyncio.sleep(0)
        return


def _turn_result(message: dict[str, object], *, prompt_tokens: int | None = None) -> TurnResult:
    return TurnResult(
        assistant_message=message,
        usage=TurnUsage(prompt_tokens=prompt_tokens),
    )


@contextmanager
def _patch_agent_conversation_store_without_history(temp_dir: str) -> Iterator[None]:
    with mock.patch("src.core.agent.ConversationStore") as store_cls:
        store_cls.find_latest_conversation_file_name.return_value = None
        store_cls.side_effect = lambda *, init_messages: ConversationStore(
            init_messages=init_messages,
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
                    init_messages=[{"role": "user", "content": "hello"}],
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
                    init_messages=[{"role": "user", "content": "hello"}],
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
                    new=mock.AsyncMock(
                        side_effect=[
                            _turn_result(ai_msg_with_tool_call, prompt_tokens=10_000),
                            _turn_result(final_ai_msg, prompt_tokens=10_000),
                        ]
                    ),
                ), mock.patch.object(Agent, "_maybe_wake_memory_manager", new=mock.AsyncMock()):
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
            ["user", "user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(stored_payload["messages"][3]["content"], "{\"echoed\": 7}")
        self.assertTrue(all("meta" not in message for message in stored_payload["messages"]))

    async def test_append_runtime_message_requires_persisted_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "hello"}],
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
                    init_messages=[{"role": "user", "content": "hello"}],
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
                init_messages=[{"role": "user", "content": "hello"}],
                tools=[self._echo_tool(), self._echo_tool()],
            )

    async def test_memory_manager_magic_word_switches_conversation(self) -> None:
        switch_events: list[list[dict[str, object]]] = []
        summarizer_runner = _StaticSummarizerRunner()
        judge_runner = _SequenceJudgeRunner([True, False])

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user-1"}],
                    tools=[self._echo_tool()],
                    on_switch_conversation=lambda *, visible_messages: switch_events.append(visible_messages),
                )
                agent._memory_manager_summarizer_runner = summarizer_runner
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
                    new=mock.AsyncMock(
                        side_effect=[
                            _turn_result(ai_msg_with_tool_call, prompt_tokens=10_000),
                            _turn_result(final_ai_msg, prompt_tokens=10_000),
                        ]
                    ),
                ), mock.patch(
                    "src.core.init_prompts.build_init_messages",
                    return_value=[{"role": "user", "content": "user-2"}],
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    agent._safe_drain_user_message_queue()
                    result = await agent.run()
                    await _wait_for_memory_manager_background_tasks(agent)
                    await _wait_for_memory_manager_reset_task(agent)

                stored_files = list(Path(temp_dir).glob("*.json"))
                self.assertEqual(len(stored_files), 2)

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(summarizer_runner.calls), 2)
        self.assertEqual(len(judge_runner.calls), 1)
        self.assertEqual(switch_events[-1], [])
        self.assertEqual(agent._messages[0], {"role": "user", "content": "user-2"})
        self.assertEqual(agent._memory_manager_summarizer_awaken_count, 0)
        self.assertEqual(agent._memory_manager_judge_awaken_count, 0)

    async def test_memory_manager_does_not_awaken_below_context_growth_threshold(self) -> None:
        summarizer_runner = _StaticSummarizerRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user"}],
                    tools=[self._echo_tool()],
                )
                agent._memory_manager_summarizer_runner = summarizer_runner
                agent._memory_manager_judge_runner = judge_runner
                agent.start_conversation()

                agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                agent._safe_drain_user_message_queue()
                store = agent._require_conversation_store()
                # 当前 tokens=2（init message + 首条用户消息），used_percent=2，
                # current_threshold=0；将 last_triggered_threshold 设为 0，确保不会被唤醒。
                store.update_memory_manager_last_triggered_threshold(last_triggered_threshold=0)

                with mock.patch("src.core.agent.get_model_context_window_tokens", return_value=100):
                    await agent._maybe_wake_memory_manager(prompt_tokens=2)

        self.assertEqual(summarizer_runner.calls, [])
        self.assertEqual(judge_runner.calls, [])
        self.assertEqual(agent._memory_manager_summarizer_awaken_count, 0)
        self.assertEqual(agent._memory_manager_judge_awaken_count, 0)

    async def test_memory_manager_awakes_when_context_growth_threshold_is_reached(self) -> None:
        summarizer_runner = _StaticSummarizerRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user"}],
                    tools=[self._echo_tool()],
                )
                agent._memory_manager_summarizer_runner = summarizer_runner
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
                    new=mock.AsyncMock(
                        side_effect=[
                            _turn_result(ai_msgs_with_tool_call[0], prompt_tokens=10),
                            _turn_result(final_ai_msg, prompt_tokens=10),
                        ]
                    ),
                ), mock.patch("src.core.agent.get_model_context_window_tokens", return_value=100):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    agent._safe_drain_user_message_queue()
                    result = await agent.run()
                    await _wait_for_memory_manager_background_tasks(agent)

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(summarizer_runner.calls), 1)
        self.assertTrue(summarizer_runner.calls[0]["is_first_time_awaken"])
        self.assertEqual(
            [tool.name for tool in summarizer_runner.calls[0]["tools"]],
            ["bash", "read_file", "replace_text", "insert_text"],
        )
        self.assertEqual(len(judge_runner.calls), 1)
        self.assertEqual(
            [tool.name for tool in judge_runner.calls[0]["tools"]],
            ["bash", "read_file", "replace_text", "insert_text"],
        )
        self.assertEqual(agent._memory_manager_summarizer_awaken_count, 1)
        self.assertEqual(agent._memory_manager_judge_awaken_count, 1)

    async def test_judge_reset_waits_for_summarizer_before_switching_conversation(self) -> None:
        switch_events: list[list[dict[str, object]]] = []
        summarizer_runner = _BlockingSummarizerRunner()
        judge_runner = _SequenceJudgeRunner([True, False])

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user-1"}],
                    tools=[self._echo_tool()],
                    on_switch_conversation=lambda *, visible_messages: switch_events.append(visible_messages),
                )
                agent._memory_manager_summarizer_runner = summarizer_runner
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
                    new=mock.AsyncMock(
                        side_effect=[
                            _turn_result(ai_msg_with_tool_call, prompt_tokens=10_000),
                            _turn_result(final_ai_msg, prompt_tokens=10_000),
                        ]
                    ),
                ), mock.patch(
                    "src.core.init_prompts.build_init_messages",
                    return_value=[{"role": "user", "content": "user-2"}],
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    agent._safe_drain_user_message_queue()
                    run_task = asyncio.create_task(agent.run())
                    await summarizer_runner.started.wait()
                    self.assertEqual(switch_events, [[]])
                    summarizer_runner.allow_finish.set()
                    result = await run_task
                    await _wait_for_memory_manager_background_tasks(agent)
                    await _wait_for_memory_manager_reset_task(agent)

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(summarizer_runner.calls), 2)
        self.assertEqual(len(judge_runner.calls), 1)
        self.assertEqual(len(switch_events), 2)
        self.assertEqual(switch_events[1], [])
        self.assertEqual(agent._messages[0], {"role": "user", "content": "user-2"})

    async def test_summarizer_does_not_reenter_while_in_flight(self) -> None:
        summarizer_runner = _BlockingSummarizerRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user-1"}],
                    tools=[self._echo_tool()],
                )
                agent._memory_manager_summarizer_runner = summarizer_runner
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
                    new=mock.AsyncMock(
                        side_effect=[
                            _turn_result(ai_msgs_with_tool_call[0], prompt_tokens=10_000),
                            _turn_result(ai_msgs_with_tool_call[1], prompt_tokens=10_000),
                            _turn_result(final_ai_msg, prompt_tokens=10_000),
                        ]
                    ),
                ):
                    agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                    agent._safe_drain_user_message_queue()
                    result = await agent.run()

                summarizer_runner.allow_finish.set()
                await _wait_for_memory_manager_background_tasks(agent)

        self.assertEqual(result, final_ai_msg)
        self.assertEqual(len(summarizer_runner.calls), 1)
        self.assertEqual(len(judge_runner.calls), 1)
        self.assertEqual(agent._memory_manager_summarizer_awaken_count, 1)
        self.assertEqual(agent._memory_manager_judge_awaken_count, 1)
        self.assertEqual(
            sum(1 for m in agent._messages if m.get("role") == "user" and m.get("content") == WAKE_MM_SUMMARY_FLAG),
            1,
        )

    async def test_reset_tail_summarizer_reuses_existing_summary_flag_boundary(self) -> None:
        summarizer_runner = _StaticSummarizerRunner()
        judge_runner = _StaticJudgeRunner(should_reset_context=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            with _patch_agent_conversation_store_without_history(temp_dir):
                agent = Agent(
                    name="demo",
                    model_config=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
                    init_messages=[{"role": "user", "content": "user-1"}],
                    tools=[self._echo_tool()],
                )
                agent._memory_manager_summarizer_runner = summarizer_runner
                agent._memory_manager_judge_runner = judge_runner
                agent.start_conversation()
                agent.enqueue_user_message(frontend_msg_id="u1", user_message="hello")
                agent._safe_drain_user_message_queue()
                agent._append_runtime_message({"role": "user", "content": WAKE_MM_SUMMARY_FLAG})
                agent._append_runtime_message({"role": "assistant", "content": "after-flag"})

                with mock.patch(
                    "src.core.init_prompts.build_init_messages",
                    return_value=[{"role": "user", "content": "user-2"}],
                ):
                    await agent._run_memory_manager_summarizer_before_reset()
                    agent._reset_context()

        self.assertEqual(len(summarizer_runner.calls), 1)
        worker_messages = summarizer_runner.calls[0]["worker_messages"]
        self.assertEqual(worker_messages[-2]["content"], WAKE_MM_SUMMARY_FLAG)
        self.assertEqual(worker_messages[-1], {"role": "assistant", "content": "after-flag"})
        self.assertEqual(agent._messages, [{"role": "user", "content": "user-2"}])


if __name__ == "__main__":
    unittest.main()
