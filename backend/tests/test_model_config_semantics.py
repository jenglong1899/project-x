import asyncio
import importlib


def test_agent_turn_codex_uses_model_config_base_url(monkeypatch) -> None:
    """
    语义一致性保护：
    - provider=openai-codex 时，应尊重 ModelConfig.base_url（而不是只读 env/默认值）。
    """
    from src.core.agent_turn import stream
    from src.core.model_config import ModelConfig

    captured: dict[str, str | None] = {"base_url": None}

    class FakeCodexClient:
        def __init__(self, *, base_url: str | None = None) -> None:
            captured["base_url"] = base_url

        async def stream_assistant_message(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"role": "assistant", "content": "ok"}

    import src.core.codex_client as codex_client_mod

    monkeypatch.setattr(codex_client_mod, "CodexClient", FakeCodexClient)

    model_config = ModelConfig(
        model="gpt-5.2",
        base_url="https://example.invalid/backend-api/codex",
        api_key="",
        provider="openai-codex",
    )

    def _noop(*args, **kwargs) -> None:
        return None

    async def _run() -> dict:
        return await stream(
            model_config=model_config,
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            on_ai_content_delta=lambda content_delta: None,
            on_ai_reasoning_delta=lambda reasoning_delta: None,
            on_ai_tool_call_started=_noop,
            on_ai_tool_call_arguments_delta=_noop,
            on_ai_tool_call_finished=_noop,
        )

    msg = asyncio.run(_run())
    assert msg.get("content") == "ok"
    assert captured["base_url"] == model_config.base_url

