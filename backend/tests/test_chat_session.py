import asyncio
import unittest
from collections.abc import Callable

from src.chat_session import AgentCallbacks, ChatSession


ScriptedRun = Callable[[AgentCallbacks, str, str], None]


class FakeAgent:
    def __init__(self, *, callbacks: AgentCallbacks, scripted_runs: list[ScriptedRun]) -> None:
        self._callbacks = callbacks
        self._scripted_runs = scripted_runs
        self._queued_messages: list[tuple[str, str]] = []

    def new_session(self) -> None:
        return None

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        self._queued_messages.append((frontend_msg_id, user_message))

    def has_pending_user_messages(self) -> bool:
        return bool(self._queued_messages)

    def run(self) -> dict[str, str]:
        user_message_id, user_message = self._queued_messages.pop(0)
        self._callbacks.on_queued_user_msg_committed(frontend_msg_id=user_message_id)
        scripted_run = self._scripted_runs.pop(0)
        scripted_run(self._callbacks, user_message_id, user_message)
        return {"role": "assistant", "content": "done"}


class ChatSessionTests(unittest.IsolatedAsyncioTestCase):
    async def _collect_events_until_generation_completed(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        while True:
            event = await asyncio.wait_for(session.next_event(), timeout=1)
            self.assertIsNotNone(event)
            events.append(event)
            if event["type"] == "generation.completed":
                return events

    async def test_chat_session_streams_message_and_tool_events(self) -> None:
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

        session = ChatSession(
            loop=asyncio.get_running_loop(),
            agent_factory=lambda *, callbacks: FakeAgent(
                callbacks=callbacks,
                scripted_runs=[scripted_run],
            ),
        )
        await session.send_session_started()
        await session.submit_user_message(user_message_id="user-1", content="你好")

        events = await self._collect_events_until_generation_completed(session)

        self.assertEqual(
            [event["type"] for event in events],
            [
                "session.started",
                "generation.started",
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
                "generation.completed",
            ],
        )
        self.assertEqual(
            events[2],
            {
                "type": "user.message.committed",
                "userMessageId": "user-1",
                "content": "你好",
            },
        )
        first_message_id = events[3]["messageId"]
        second_message_id = events[11]["messageId"]
        self.assertNotEqual(first_message_id, second_message_id)
        self.assertEqual(events[4]["messageId"], first_message_id)
        self.assertEqual(events[5]["messageId"], first_message_id)
        self.assertEqual(events[6]["messageId"], first_message_id)
        self.assertEqual(events[12]["messageId"], second_message_id)
        self.assertEqual(events[13]["messageId"], second_message_id)
        self.assertEqual(events[7]["toolCallId"], "call_1")
        self.assertEqual(
            events[8],
            {
                "type": "tool.arguments.delta",
                "toolCallId": "call_1",
                "toolName": "bash",
                "argumentsDelta": '{"command":"pwd"}',
            },
        )
        self.assertEqual(
            events[9],
            {
                "type": "tool.completed",
                "toolCallId": "call_1",
                "toolName": "bash",
                "arguments": '{"command":"pwd"}',
            },
        )
        self.assertEqual(events[10]["result"], '{"stdout":"/tmp"}')

        await session.close()

    async def test_chat_session_drains_multiple_user_messages_in_one_generation(self) -> None:
        def make_scripted_run(reply_text: str) -> ScriptedRun:
            def scripted_run(
                callbacks: AgentCallbacks,
                _user_message_id: str,
                _content: str,
            ) -> None:
                callbacks.on_ai_content_delta(content_delta=reply_text)

            return scripted_run

        session = ChatSession(
            loop=asyncio.get_running_loop(),
            agent_factory=lambda *, callbacks: FakeAgent(
                callbacks=callbacks,
                scripted_runs=[
                    make_scripted_run("第一条回复"),
                    make_scripted_run("第二条回复"),
                ],
            ),
        )
        await session.send_session_started()
        await session.submit_user_message(user_message_id="user-1", content="第一条")
        await session.submit_user_message(user_message_id="user-2", content="第二条")

        events = await self._collect_events_until_generation_completed(session)

        self.assertEqual(
            [event["type"] for event in events if event["type"].startswith("generation.")],
            ["generation.started", "generation.completed"],
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


if __name__ == "__main__":
    unittest.main()
