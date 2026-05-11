import asyncio
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from unittest import mock

from src.core.agent_base import AgentBase
from src.core.agent_controller import AgentController
from src.core.model_config import ModelConfig
from src.websocket_chat_session import AgentCallbacks, WebSocketChatSession, create_default_agent


ScriptedRun = Callable[[AgentCallbacks, str, str], None]


def noop_callback(**_kwargs: object) -> None:
    return None


def make_noop_agent_callbacks() -> AgentCallbacks:
    return AgentCallbacks(
        on_ai_content_delta=noop_callback,
        on_ai_reasoning_delta=noop_callback,
        on_ai_tool_call_started=noop_callback,
        on_ai_tool_call_arguments_delta=noop_callback,
        on_ai_tool_call_finished=noop_callback,
        on_tool_result=noop_callback,
        on_queued_user_msg_committed=noop_callback,
        on_switch_conversation=noop_callback,
    )


class FakeAgent(AgentBase):
    def __init__(
        self,
        *,
        callbacks: AgentCallbacks,
        scripted_runs: list[ScriptedRun],
        start_visible_messages: list[dict[str, object]] | None,
    ) -> None:
        self._callbacks = callbacks
        self._scripted_runs = scripted_runs
        self._queued_messages: list[tuple[str, str]] = []
        self._start_visible_messages = start_visible_messages

    def start_conversation(self) -> None:
        if self._start_visible_messages is not None:
            self._callbacks.on_switch_conversation(
                visible_messages=self._start_visible_messages,
            )
        return None

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        self._queued_messages.append((frontend_msg_id, user_message))

    def has_pending_user_messages(self) -> bool:
        return bool(self._queued_messages)

    async def run(self) -> dict[str, str]:
        user_message_id, user_message = self._queued_messages.pop(0)
        self._callbacks.on_queued_user_msg_committed(frontend_msg_id=user_message_id)
        scripted_run = self._scripted_runs.pop(0)
        scripted_run(self._callbacks, user_message_id, user_message)
        return {"role": "assistant", "content": "done"}


def make_agent_controller_factory(
    *,
    scripted_runs: list[ScriptedRun],
    start_visible_messages: list[dict[str, object]] | None = None,
):
    def factory(
        *,
        callbacks: AgentCallbacks,
        is_closed: Callable[[], bool],
        on_agent_became_busy: Callable[[], None],
        on_agent_turn_completed: Callable[[], None],
        on_agent_became_idle: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> AgentController:
        controller = AgentController(
            agent=FakeAgent(
                callbacks=callbacks,
                scripted_runs=list(scripted_runs),
                start_visible_messages=start_visible_messages,
            ),
            is_closed=is_closed,
            on_agent_became_busy=on_agent_became_busy,
            on_agent_turn_completed=on_agent_turn_completed,
            on_agent_became_idle=on_agent_became_idle,
            on_error=on_error,
        )
        controller.start()
        return controller

    return factory


class WebSocketChatSessionTests(unittest.IsolatedAsyncioTestCase):
    def test_create_default_agent_passes_main_memory_snapshot_and_default_tools(self) -> None:
        fake_cwd_state = SimpleNamespace(cwd="/tmp")
        fake_bash_tool = SimpleNamespace(name="bash")
        fake_read_file_tool = SimpleNamespace(name="read_file")
        fake_replace_text_tool = SimpleNamespace(name="replace_text")

        with mock.patch(
            "src.websocket_chat_session.read_main_memory",
            return_value="main memory snapshot",
        ) as read_main_memory, mock.patch(
            "src.websocket_chat_session.build_system_level_instruction_zh",
            return_value="system",
        ), mock.patch(
            "src.websocket_chat_session.build_user_level_instruction_zh",
            return_value="user",
        ), mock.patch(
            "src.websocket_chat_session.resolve_model_config",
            return_value=ModelConfig(model="demo", base_url="https://example.com", api_key="key"),
        ), mock.patch(
            "src.websocket_chat_session.CwdState",
            return_value=fake_cwd_state,
        ), mock.patch(
            "src.websocket_chat_session.create_bash_tool",
            return_value=fake_bash_tool,
        ) as create_bash_tool, mock.patch(
            "src.websocket_chat_session.create_read_file_tool",
            return_value=fake_read_file_tool,
        ) as create_read_file_tool, mock.patch(
            "src.websocket_chat_session.create_replace_text_tool",
            return_value=fake_replace_text_tool,
        ) as create_replace_text_tool, mock.patch("src.websocket_chat_session.Agent") as agent_cls:
            create_default_agent(callbacks=make_noop_agent_callbacks())

        create_bash_tool.assert_called_once_with(cwd_state=fake_cwd_state)
        create_read_file_tool.assert_called_once_with(cwd_provider=fake_cwd_state)
        create_replace_text_tool.assert_called_once_with(cwd_provider=fake_cwd_state)

        read_main_memory.assert_called_once_with()
        agent_kwargs = agent_cls.call_args.kwargs
        self.assertEqual(agent_kwargs["loaded_main_memory_content"], "main memory snapshot")
        self.assertEqual([tool.name for tool in agent_kwargs["tools"]], ["bash", "read_file", "replace_text"])
        self.assertNotIn("reset_context", [tool.name for tool in agent_kwargs["tools"]])

    async def _collect_events_until_generation_completed(
        self,
        session: WebSocketChatSession,
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        while True:
            event = await asyncio.wait_for(session.next_event(), timeout=1)
            self.assertIsNotNone(event)
            events.append(event)
            if event["type"] == "agent.became.idle":
                return events

    async def test_websocket_chat_session_streams_message_and_tool_events(self) -> None:
        def scripted_run(callbacks: AgentCallbacks, _user_message_id: str, _content: str) -> None:
            callbacks.on_ai_reasoning_delta(reasoning_delta="先想")
            callbacks.on_ai_content_delta(content_delta="先说")
            callbacks.on_ai_tool_call_started(
                index=0,
                tool_call_id="call_1",
                tool_name="bash",
            )
            callbacks.on_ai_tool_call_arguments_delta(
                index=0,
                tool_call_id="call_1",
                tool_name="bash",
                arguments_delta='{"command":"pwd"}',
            )
            callbacks.on_ai_tool_call_finished(
                index=0,
                tool_call_id="call_1",
                tool_name="bash",
                arguments='{"command":"pwd"}',
            )
            callbacks.on_tool_result(
                tool_call_id="call_1",
                result_json_str='{"stdout":"/tmp"}',
            )
            callbacks.on_ai_content_delta(content_delta="后说")

        session = WebSocketChatSession(
            agent_controller_factory=make_agent_controller_factory(
                scripted_runs=[scripted_run],
            ),
        )
        await session.submit_user_message(user_message_id="user-1", content="你好")

        events = await self._collect_events_until_generation_completed(session)

        self.assertEqual(
            [event["type"] for event in events],
            [
                "agent.became.busy",
                "user.message.committed",
                "assistant.message.started",
                "assistant.message.delta",
                "assistant.message.delta",
                "assistant.message.completed",
                "tool.started",
                "tool.arguments.delta",
                "tool.completed",
                "tool.result",
                "assistant.message.started",
                "assistant.message.delta",
                "assistant.message.completed",
                "agent.became.idle",
            ],
        )
        self.assertEqual(
            events[1],
            {
                "type": "user.message.committed",
                "userMessageId": "user-1",
                "content": "你好",
            },
        )
        first_message_id = events[2]["messageId"]
        second_message_id = events[10]["messageId"]
        self.assertNotEqual(first_message_id, second_message_id)
        self.assertEqual(events[3]["messageId"], first_message_id)
        self.assertEqual(events[4]["messageId"], first_message_id)
        self.assertEqual(events[5]["messageId"], first_message_id)
        self.assertEqual(events[11]["messageId"], second_message_id)
        self.assertEqual(events[12]["messageId"], second_message_id)
        self.assertEqual(events[6]["toolCallId"], "call_1")
        self.assertEqual(
            events[7],
            {
                "type": "tool.arguments.delta",
                "toolCallId": "call_1",
                "toolName": "bash",
                "argumentsDelta": '{"command":"pwd"}',
            },
        )
        self.assertEqual(
            events[8],
            {
                "type": "tool.completed",
                "toolCallId": "call_1",
                "toolName": "bash",
                "arguments": '{"command":"pwd"}',
            },
        )
        self.assertEqual(events[9]["result"], '{"stdout":"/tmp"}')

        await session.close()

    async def test_websocket_chat_session_drains_multiple_user_messages_in_one_generation(
        self,
    ) -> None:
        def make_scripted_run(reply_text: str) -> ScriptedRun:
            def scripted_run(
                callbacks: AgentCallbacks,
                _user_message_id: str,
                _content: str,
            ) -> None:
                callbacks.on_ai_content_delta(content_delta=reply_text)

            return scripted_run

        session = WebSocketChatSession(
            agent_controller_factory=make_agent_controller_factory(
                scripted_runs=[
                    make_scripted_run("第一条回复"),
                    make_scripted_run("第二条回复"),
                ],
            ),
        )
        await session.submit_user_message(user_message_id="user-1", content="第一条")
        await session.submit_user_message(user_message_id="user-2", content="第二条")

        events = await self._collect_events_until_generation_completed(session)

        self.assertEqual(
            [event["type"] for event in events if event["type"].startswith("agent.became.")],
            ["agent.became.busy", "agent.became.idle"],
        )
        self.assertEqual(
            [
                (event["userMessageId"], event["content"])
                for event in events
                if event["type"] == "user.message.committed"
            ],
            [("user-1", "第一条"), ("user-2", "第二条")],
        )
        assistant_started_events = [
            event for event in events if event["type"] == "assistant.message.started"
        ]
        self.assertEqual(len(assistant_started_events), 2)

        await session.close()

    async def test_websocket_chat_session_emits_initial_conversation_switched_on_start(
        self,
    ) -> None:
        session = WebSocketChatSession(
            agent_controller_factory=make_agent_controller_factory(
                scripted_runs=[],
                start_visible_messages=[
                    {"role": "user", "content": "上一轮问题"},
                    {"role": "assistant", "content": "上一轮回答"},
                ],
            ),
        )

        event = await asyncio.wait_for(session.next_event(), timeout=1)

        self.assertEqual(
            event,
            {
                "type": "conversation.switched",
                "visibleMessages": [
                    {"role": "user", "content": "上一轮问题"},
                    {"role": "assistant", "content": "上一轮回答"},
                ],
            },
        )

        await session.close()

    async def test_websocket_chat_session_emits_conversation_switched_before_next_delta(self) -> None:
        def scripted_run(callbacks: AgentCallbacks, _user_message_id: str, _content: str) -> None:
            callbacks.on_switch_conversation(
                visible_messages=[{"role": "user", "content": "<auto_reminder>继续之前的任务</auto_reminder>"}],
            )
            callbacks.on_ai_content_delta(content_delta="新会话开始输出")

        session = WebSocketChatSession(
            agent_controller_factory=make_agent_controller_factory(
                scripted_runs=[scripted_run],
            ),
        )
        await session.submit_user_message(user_message_id="user-1", content="触发 reset")

        events = await self._collect_events_until_generation_completed(session)

        types = [event["type"] for event in events]
        switched_index = types.index("conversation.switched")
        assistant_delta_index = types.index("assistant.message.delta", switched_index + 1)

        self.assertLess(switched_index, assistant_delta_index)
        self.assertEqual(
            events[switched_index]["visibleMessages"],
            [{"role": "user", "content": "<auto_reminder>继续之前的任务</auto_reminder>"}],
        )

        await session.close()


if __name__ == "__main__":
    unittest.main()
