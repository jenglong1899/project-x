import asyncio
import json
import os
import socket
import threading
import time
from dataclasses import dataclass

import httpx
import pytest
import websockets


pytestmark = pytest.mark.skipif(
    os.getenv("PROJECT_X_RUN_REAL_API_TESTS", "").strip() != "1",
    reason="默认跳过真实 API/端到端集成测试；如需运行请设置环境变量 PROJECT_X_RUN_REAL_API_TESTS=1。",
)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True)
class AssistantMessage:
    content: str


class _UvicornServerThread:
    def __init__(self, *, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server = None
        self._thread = None

    def start(self) -> None:
        from src.web_app import build_app
        import uvicorn

        config = uvicorn.Config(
            app=build_app(),
            host=self._host,
            port=self._port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config=config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def _wait_until_ready(*, origin: str, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{origin}/healthz", timeout=1.0)
            if r.status_code == 200 and (r.json() or {}).get("ok") is True:
                return
        except Exception as exc:
            last_exc = exc
        time.sleep(0.1)
    raise RuntimeError(f"服务未就绪：origin={origin} last_exc={last_exc!r}")


def _ensure_real_model_ready_or_skip() -> None:
    from src.websocket_chat_session import resolve_model_config

    model_key = os.getenv("PROJECT_X_MODEL_CONFIG", "").strip()
    if model_key == "mock":
        pytest.skip("当前 PROJECT_X_MODEL_CONFIG=mock，本测试要求真实模型。")
    try:
        resolve_model_config()
    except Exception as exc:
        pytest.skip(f"真实模型不可用，跳过：{type(exc).__name__}: {exc}")


async def _run_ws_flow(*, ws_url: str) -> tuple[list[AssistantMessage], list[dict]]:
    # 注意：服务端链路是“真实的”——测试会用 uvicorn 启动 `src.web_app.build_app()` 并连接 `/ws`。
    # 这里这套“收发 JSON + 组装 delta + 等 committed/tool 事件”的逻辑属于测试侧的最小 WS 客户端，
    # 用来模拟真实前端的行为并做断言（项目里目前没有可直接复用的 Python WS 客户端实现）。
    assistant_messages: list[AssistantMessage] = []
    tool_events: list[dict] = []

    active_assistant_chunks: list[str] | None = None

    async with websockets.connect(ws_url, open_timeout=5, close_timeout=5) as ws:
        async def _recv_event(*, timeout_s: float = 5) -> dict:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
            return json.loads(raw)

        async def _send_user(user_message_id: str, content: str) -> None:
            await ws.send(
                json.dumps(
                    {
                        "type": "send_user_message",
                        "userMessageId": user_message_id,
                        "content": content,
                    },
                    ensure_ascii=False,
                )
            )

        async def _send_user_and_wait_committed(user_message_id: str, content: str) -> None:
            await _send_user(user_message_id, content)
            deadline = time.time() + 20
            while time.time() < deadline:
                event = await _recv_event(timeout_s=5)
                if event.get("type") == "user.message.committed" and event.get("userMessageId") == user_message_id:
                    return
                _handle_non_committed_event(event)
            raise AssertionError(f"未等到 user.message.committed：userMessageId={user_message_id}")

        def _handle_non_committed_event(event: dict) -> None:
            nonlocal active_assistant_chunks

            event_type = event.get("type")

            if event_type == "assistant.message.started":
                active_assistant_chunks = []
                return
            if event_type == "assistant.message.delta" and event.get("channel") == "content":
                if active_assistant_chunks is not None:
                    active_assistant_chunks.append(str(event.get("delta") or ""))
                return
            if event_type == "assistant.message.completed":
                if active_assistant_chunks is not None:
                    assistant_messages.append(AssistantMessage(content="".join(active_assistant_chunks)))
                active_assistant_chunks = None
                return

            if event_type in {"tool.started", "tool.completed", "tool.result"}:
                tool_events.append(event)
                return

            if event_type == "error":
                raise RuntimeError(f"服务返回 error 事件：{event!r}")

        await _send_user_and_wait_committed("m1", "hi, 我叫小明")
        await asyncio.sleep(0.2)
        await _send_user_and_wait_committed("m2", "你知道我叫什么名字吗？")
        await asyncio.sleep(0.2)
        await _send_user_and_wait_committed("m3", "请调用bash工具查看当前时间。你必须调用 bash，并把结果告诉我。")

        deadline = time.time() + 60
        while time.time() < deadline:
            event = await _recv_event(timeout_s=5)
            _handle_non_committed_event(event)
            if event.get("type") == "tool.result":
                result = json.loads(event.get("result") or "{}")
                if isinstance(result, dict) and "stdout" in result:
                    return assistant_messages, tool_events

    raise AssertionError("未在超时时间内拿到 bash tool 的结果（tool.result.stdout）。")


@pytest.mark.integration
def test_websocket_real_model_tool_call_roundtrip() -> None:
    """
    真实集成测试（不 mock）：通过 web_app.py 的 /ws 端点跑三轮对话，并验证 bash 工具调用闭环。
    """
    _ensure_real_model_ready_or_skip()

    host = "127.0.0.1"
    port = _pick_free_port()
    origin = f"http://{host}:{port}"
    ws_url = f"ws://{host}:{port}/ws"

    server = _UvicornServerThread(host=host, port=port)
    server.start()
    try:
        _wait_until_ready(origin=origin)
        assistant_messages, tool_events = asyncio.run(_run_ws_flow(ws_url=ws_url))
    finally:
        server.stop()

    all_assistant_text = "\n".join(m.content for m in assistant_messages)
    assert "小明" in all_assistant_text

    tool_names = [e.get("toolName") for e in tool_events if e.get("type") == "tool.started"]
    assert "bash" in tool_names
