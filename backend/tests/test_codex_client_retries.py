import asyncio

import httpx
import pytest


class _FakeResponse:
    def __init__(self, *, chunks: list[str], raise_after_chunks: int | None = None) -> None:
        self._chunks = chunks
        self._raise_after_chunks = raise_after_chunks

    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self):
        for i, chunk in enumerate(self._chunks, start=1):
            yield chunk
            if self._raise_after_chunks is not None and i >= self._raise_after_chunks:
                raise httpx.ReadTimeout("fake read timeout")


class _FakeStreamContext:
    def __init__(self, resp: _FakeResponse) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeResponse:
        return self._resp

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeAsyncClient:
    def __init__(self, *, timeout: httpx.Timeout, **_kwargs) -> None:
        self._timeout = timeout
        self.stream_calls: list[tuple[str, str]] = []
        self._attempt = 0

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def stream(self, method: str, url: str, *, headers: dict, json: dict) -> _FakeStreamContext:
        self._attempt += 1
        self.stream_calls.append((method, url))
        raise AssertionError("测试需要 monkeypatch 在实例上替换 stream() 行为")


def test_retries_when_no_events_emitted(monkeypatch) -> None:
    """
    回归测试：第一次请求在拿到任何 SSE 事件前就 ReadTimeout，应当自动重试。
    """
    from src.core.codex_client import CodexClient
    import src.core.codex_client as codex_client_mod

    clients: list[_FakeAsyncClient] = []

    def _fake_async_client(*, timeout: httpx.Timeout, **kwargs):
        client = _FakeAsyncClient(timeout=timeout)
        clients.append(client)
        return client

    monkeypatch.setattr(codex_client_mod.httpx, "AsyncClient", _fake_async_client)
    monkeypatch.setenv("PROJECT_X_CODEX_HTTP_MAX_RETRIES", "2")
    monkeypatch.setenv("PROJECT_X_CODEX_HTTP_RETRY_BACKOFF_S", "0")

    client = object.__new__(CodexClient)
    client._runtime = type("R", (), {"access_token": "t", "base_url": "https://example.test"})()

    stream_attempt = {"n": 0}

    def _install_stream_behavior(fake_client: _FakeAsyncClient) -> None:
        def _stream(method: str, url: str, *, headers: dict, json: dict):
            stream_attempt["n"] += 1
            if stream_attempt["n"] == 1:
                raise httpx.ReadTimeout("fake read timeout")
            resp = _FakeResponse(
                chunks=[
                    'data: {"type":"response.output_text.delta","delta":"OK"}\n\n',
                    'data: {"type":"response.completed","response":{"output":[]}}\n\n',
                ]
            )
            return _FakeStreamContext(resp)

        fake_client.stream = _stream  # type: ignore[method-assign]

    async def _run() -> dict:
        msg_parts: list[str] = []

        assistant_msg = await client.stream_assistant_message(
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            on_text_delta=msg_parts.append,
            on_reasoning_delta=lambda _: None,
            on_tool_call_delta=None,
        )
        return {"assistant_msg": assistant_msg, "deltas": "".join(msg_parts)}

    # 每次进入 async with 都会新建一个 fake client，所以这里用 hook 去装 stream 行为
    def _wrapped_async_client(*, timeout: httpx.Timeout, **kwargs):
        c = _fake_async_client(timeout=timeout, **kwargs)
        _install_stream_behavior(c)
        return c

    monkeypatch.setattr(codex_client_mod.httpx, "AsyncClient", _wrapped_async_client)

    result = asyncio.run(_run())
    assert result["assistant_msg"]["content"] == "OK"
    assert result["deltas"] == "OK"
    assert stream_attempt["n"] == 2


def test_does_not_retry_after_partial_stream(monkeypatch) -> None:
    """
    如果已经开始吐出 SSE 事件，再发生 ReadTimeout，重试会导致前端内容重复，因此不重试。
    """
    from src.core.codex_client import CodexClient
    import src.core.codex_client as codex_client_mod

    monkeypatch.setenv("PROJECT_X_CODEX_HTTP_MAX_RETRIES", "2")
    monkeypatch.setenv("PROJECT_X_CODEX_HTTP_RETRY_BACKOFF_S", "0")

    def _fake_async_client(*, timeout: httpx.Timeout, **kwargs):
        client = _FakeAsyncClient(timeout=timeout)

        def _stream(method: str, url: str, *, headers: dict, json: dict):
            resp = _FakeResponse(
                chunks=[
                    'data: {"type":"response.output_text.delta","delta":"H"}\n\n',
                ],
                raise_after_chunks=1,
            )
            return _FakeStreamContext(resp)

        client.stream = _stream  # type: ignore[method-assign]
        return client

    monkeypatch.setattr(codex_client_mod.httpx, "AsyncClient", _fake_async_client)

    client = object.__new__(CodexClient)
    client._runtime = type("R", (), {"access_token": "t", "base_url": "https://example.test"})()

    async def _run() -> None:
        await client.stream_assistant_message(
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            on_text_delta=lambda _: None,
            on_reasoning_delta=lambda _: None,
            on_tool_call_delta=None,
        )

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(_run())
