import os
import asyncio

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("PROJECT_X_RUN_REAL_API_TESTS", "").strip() != "1",
    reason="默认跳过真实 API 集成测试；如需运行请设置环境变量 PROJECT_X_RUN_REAL_API_TESTS=1。",
)


@pytest.mark.integration
def test_codex_stream_real_api_smoke() -> None:
    """
    真实集成测试（不 mock）：验证我们能用 OAuth token 调通 Codex backend 并解析 SSE。
    """
    from src.pkg.openai_codex.auth import resolve_codex_tokens
    from src.pkg.openai_codex.client import CodexClient

    try:
        resolve_codex_tokens(import_from_cli_if_missing=True)
    except Exception as exc:
        pytest.skip(f"未配置 Codex OAuth 凭据，跳过真实集成测试：{type(exc).__name__}: {exc}")

    client = CodexClient()

    content_deltas: list[str] = []
    reasoning_deltas: list[str] = []

    async def _run() -> dict:
        return await client.stream_assistant_message(
            model=os.getenv("PROJECT_X_CODEX_MODEL", "gpt-5.2").strip() or "gpt-5.2",
            messages=[
                {"role": "system", "content": "你是一个简洁的助手。"},
                {"role": "user", "content": "回复我一个数字 7。只输出 7。"},
            ],
            tools=[],
            on_text_delta=lambda delta: content_deltas.append(delta),
            on_reasoning_delta=lambda delta: reasoning_deltas.append(delta),
        )

    msg = asyncio.run(_run())
    assert isinstance(msg, dict)
    assert msg.get("role") == "assistant"
    assert msg.get("content") is not None
    assert "".join(content_deltas).strip() != ""


@pytest.mark.integration
def test_codex_real_api_tool_call_roundtrip() -> None:
    """
    真实集成测试（不 mock）：验证 tool call 闭环能跑通。

    目标链路：
    user -> assistant(function_call) -> tool(function_call_output) -> assistant(引用 tool 输出)
    """
    from src.pkg.openai_codex.auth import resolve_codex_tokens
    from src.core.model_config import ModelConfig
    from src.core.agent_turn import Tool, execute_tool_calls, stream

    try:
        resolve_codex_tokens(import_from_cli_if_missing=True)
    except Exception as exc:
        pytest.skip(f"未配置 Codex OAuth 凭据，跳过真实集成测试：{type(exc).__name__}: {exc}")

    model_config = ModelConfig(
        model=os.getenv("PROJECT_X_CODEX_MODEL", "gpt-5.2").strip() or "gpt-5.2",
        base_url=os.getenv("PROJECT_X_CODEX_BASE_URL", "https://chatgpt.com/backend-api/codex").strip().rstrip("/"),
        api_key="",
        provider="openai-codex",
    )

    async def echo_tool(*, arguments: dict) -> str:
        text = str(arguments.get("text") or "")
        return text

    tools = [
        Tool(
            name="echo_tool",
            description="回显输入文本。",
            parameters_json_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=echo_tool,
        )
    ]

    async def _run() -> None:
        messages: list[dict] = [
            {"role": "system", "content": "你是一个严格遵循指令的助手。"},
            {
                "role": "user",
                "content": (
                    "你必须先调用工具 echo_tool，参数为 {\"text\": \"ping\"}。\n"
                    "拿到工具输出后，只输出一行：TOOL_RESULT: <工具输出>。\n"
                    "禁止输出其它任何内容。"
                ),
            },
        ]

        content_deltas: list[str] = []
        reasoning_deltas: list[str] = []

        def _noop(*args, **kwargs) -> None:
            return None

        first_ai = await stream(
            model_config=model_config,
            messages=messages,
            tools=tools,
            on_ai_content_delta=lambda content_delta: content_deltas.append(content_delta),
            on_ai_reasoning_delta=lambda reasoning_delta: reasoning_deltas.append(reasoning_delta),
            on_ai_tool_call_started=_noop,
            on_ai_tool_call_arguments_delta=_noop,
            on_ai_tool_call_finished=_noop,
        )

        tool_calls = first_ai.get("tool_calls") or []
        if not isinstance(tool_calls, list) or not tool_calls:
            raise AssertionError(f"期望产生 tool_calls，但实际没有。first_ai={first_ai!r}")

        messages.append(first_ai)
        tool_messages = await execute_tool_calls(
            ai_msg_dict=first_ai,
            tools=tools,
            on_tool_result=_noop,
        )
        messages.extend(tool_messages)

        second_content_deltas: list[str] = []
        second_ai = await stream(
            model_config=model_config,
            messages=messages,
            tools=tools,
            on_ai_content_delta=lambda content_delta: second_content_deltas.append(content_delta),
            on_ai_reasoning_delta=_noop,
            on_ai_tool_call_started=_noop,
            on_ai_tool_call_arguments_delta=_noop,
            on_ai_tool_call_finished=_noop,
        )

        final_text = str(second_ai.get("content") or "").strip()
        assert final_text == "TOOL_RESULT: ping"

    asyncio.run(_run())
