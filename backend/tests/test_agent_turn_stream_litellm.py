import unittest
from unittest import mock

from src.core.agent_turn import Tool, stream
from src.core.model_config import ModelConfig


async def _noop_tool_handler(*, arguments: dict[str, object]) -> dict[str, object]:
    _ = arguments
    return {"ok": True}


class AgentTurnStreamLiteLLMTests(unittest.IsolatedAsyncioTestCase):

    @staticmethod
    def _echo_tool() -> Tool:
        return Tool(
            name="echo",
            description="回显",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "value": {"type": "integer"},
                },
                "required": ["value"],
            },
            handler=_noop_tool_handler,
        )

    async def test_stream_litellm_emits_tool_call_events_and_returns_tool_calls(self) -> None:
        model_config = ModelConfig(
            model="fake-model",
            base_url="https://example.invalid/litellm",
            api_key="fake-key",
            provider="litellm",
        )

        content_deltas: list[str] = []
        reasoning_deltas: list[str] = []
        started: list[tuple[int, str | None, str | None]] = []
        arguments_deltas: list[tuple[int, str | None, str | None, str]] = []
        finished: list[tuple[int, str | None, str | None, str]] = []

        async def _fake_acompletion(**kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs

            async def _gen():  # type: ignore[no-untyped-def]
                yield {
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "content": "hi",
                                "reasoning_content": "r1",
                            }
                        }
                    ]
                }
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "echo",
                                            "arguments": "{\"value\":",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": "1}",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }

            return _gen()

        with mock.patch("src.core.agent_turn.acompletion", _fake_acompletion):
            msg = await stream(
                model_config=model_config,
                messages=[{"role": "user", "content": "x"}],
                tools=[self._echo_tool()],
                on_ai_content_delta=lambda *, content_delta: content_deltas.append(content_delta),
                on_ai_reasoning_delta=lambda *, reasoning_delta: reasoning_deltas.append(reasoning_delta),
                on_ai_tool_call_started=lambda *, index, tool_call_id, tool_name: started.append(
                    (index, tool_call_id, tool_name)
                ),
                on_ai_tool_call_arguments_delta=lambda *, index, tool_call_id, tool_name, arguments_delta: arguments_deltas.append(
                    (index, tool_call_id, tool_name, arguments_delta)
                ),
                on_ai_tool_call_finished=lambda *, index, tool_call_id, tool_name, arguments: finished.append(
                    (index, tool_call_id, tool_name, arguments)
                ),
            )

        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["content"], "hi")
        self.assertEqual(msg["reasoning_content"], "r1")

        self.assertIn("tool_calls", msg)
        tool_calls = msg["tool_calls"]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["id"], "call_1")
        self.assertEqual(tool_calls[0]["function"]["name"], "echo")
        self.assertEqual(tool_calls[0]["function"]["arguments"], "{\"value\":1}")

        self.assertEqual(content_deltas, ["hi"])
        self.assertEqual(reasoning_deltas, ["r1"])
        self.assertEqual(started, [(0, "call_1", "echo")])
        self.assertEqual(arguments_deltas, [(0, "call_1", "echo", "{\"value\":"), (0, "call_1", "echo", "1}")])
        self.assertEqual(finished, [(0, "call_1", "echo", "{\"value\":1}")])
