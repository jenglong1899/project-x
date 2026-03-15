import asyncio
import json
import logging
import os
import threading
from contextlib import suppress
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.core.agent import Agent
from src.core.model_config import (
    DEEPSEEK,
    GEMINI,
    GEMINI_OPENROUTER,
    MINIMAX_MAINLAND,
    MINIMAX_OVERSEA,
    QWEN35PLUS,
    ModelConfig,
)
from src.tools.bash import BASH_TOOL


logger = logging.getLogger(__name__)

MODEL_CONFIGS: dict[str, ModelConfig] = {
    "qwen35plus": QWEN35PLUS,
    "deepseek": DEEPSEEK,
    "gemini": GEMINI,
    "gemini_openrouter": GEMINI_OPENROUTER,
    "minimax_mainland": MINIMAX_MAINLAND,
    "minimax_oversea": MINIMAX_OVERSEA,
}

SYSTEM_INSTRUCTION = (
    "请直接回答用户问题；需要执行命令时优先使用工具；"
    "除非用户要求，否则不要展示多余铺垫。"
)
USER_INSTRUCTION = "你正在通过 Web 端与用户持续对话。"


class SendUserMessageCommand(BaseModel):
    type: Literal["send_user_message"]
    userTurnId: str
    content: str


class PingCommand(BaseModel):
    type: Literal["ping"]


ClientCommand = SendUserMessageCommand | PingCommand


def parse_client_command(payload: dict[str, Any]) -> ClientCommand:
    command_type = payload.get("type")
    if command_type == "send_user_message":
        return SendUserMessageCommand.model_validate(payload)
    if command_type == "ping":
        return PingCommand.model_validate(payload)
    raise ValueError(f"不支持的 command.type: {command_type}")


def resolve_model_config() -> ModelConfig:
    model_key = os.getenv("BIONIC_CLAW_MODEL_CONFIG", "qwen35plus")
    model_config = MODEL_CONFIGS.get(model_key)
    if model_config is None:
        supported = ", ".join(sorted(MODEL_CONFIGS))
        raise RuntimeError(
            f"BIONIC_CLAW_MODEL_CONFIG={model_key} 不受支持，可选值: {supported}"
        )
    if not model_config.api_key:
        raise RuntimeError(f"{model_key} 对应的 API key 未配置")
    return model_config


class ChatSession:
    def __init__(self, *, loop: asyncio.AbstractEventLoop) -> None:
        self.session_id = uuid4().hex
        self._loop = loop
        self._outgoing_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._run_thread_lock = threading.Lock()
        self._run_thread: threading.Thread | None = None
        self._active_assistant_turn_id: str | None = None
        self._tool_call_ids_by_index: dict[int, str] = {}
        self._closed = False

        self._agent = Agent(
            name="bionic-claw-web",
            model_config=resolve_model_config(),
            system_instruction=SYSTEM_INSTRUCTION,
            user_instruction=USER_INSTRUCTION,
            tools=[BASH_TOOL],
            on_ai_content_delta=self._on_ai_content_delta,
            on_ai_reasoning_delta=self._on_ai_reasoning_delta,
            on_ai_tool_call_started=self._on_ai_tool_call_started,
            on_ai_tool_call_arguments_delta=self._on_ai_tool_call_arguments_delta,
            on_ai_tool_call_finished=self._on_ai_tool_call_finished,
            on_tool_result=self._on_tool_result,
            on_user_msg_enqueued=self._on_user_msg_enqueued,
            on_queued_user_msg_committed=self._on_queued_user_msg_committed,
        )
        self._agent.new_session()

    async def send_session_started(self) -> None:
        await self._emit(
            {
                "type": "session.started",
                "sessionId": self.session_id,
            }
        )

    async def next_event(self) -> dict[str, Any] | None:
        return await self._outgoing_queue.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._outgoing_queue.put(None)

    def enqueue_user_message(self, *, user_turn_id: str, content: str) -> None:
        self._agent.enqueue_user_message(
            frontend_msg_id=user_turn_id,
            user_message=content,
        )
        self._ensure_run_thread()

    async def _emit(self, event: dict[str, Any]) -> None:
        await self._outgoing_queue.put(event)

    def _emit_from_thread(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        self._loop.call_soon_threadsafe(self._outgoing_queue.put_nowait, event)

    def _emit_error_from_thread(self, *, code: str, message: str) -> None:
        self._emit_from_thread(
            {
                "type": "error",
                "code": code,
                "message": message,
            }
        )

    def _ensure_run_thread(self) -> None:
        with self._run_thread_lock:
            if self._run_thread is not None and self._run_thread.is_alive():
                return

            run_thread = threading.Thread(
                target=self._run_agent_until_idle,
                name=f"chat-session-{self.session_id}",
                daemon=True,
            )
            self._run_thread = run_thread
            run_thread.start()

    def _run_agent_until_idle(self) -> None:
        while True:
            assistant_turn_id = uuid4().hex
            self._active_assistant_turn_id = assistant_turn_id
            self._tool_call_ids_by_index = {}
            self._emit_from_thread(
                {
                    "type": "assistant.turn.started",
                    "assistantTurnId": assistant_turn_id,
                }
            )

            try:
                self._agent.run()
            except Exception as exc:
                logger.exception("ChatSession agent.run 失败")
                self._emit_error_from_thread(
                    code="agent_run_failed",
                    message=str(exc),
                )
                self._active_assistant_turn_id = None
                self._tool_call_ids_by_index = {}
                return

            self._emit_from_thread(
                {
                    "type": "assistant.turn.completed",
                    "assistantTurnId": assistant_turn_id,
                }
            )
            self._active_assistant_turn_id = None
            self._tool_call_ids_by_index = {}

            if not self._agent.has_pending_user_messages():
                return

    def _current_assistant_turn_id(self) -> str:
        if self._active_assistant_turn_id is None:
            raise RuntimeError("assistant turn 尚未初始化")
        return self._active_assistant_turn_id

    def _resolve_tool_call_id(self, *, index: int, tool_call_id: str | None) -> str:
        if tool_call_id:
            self._tool_call_ids_by_index[index] = tool_call_id
            return tool_call_id

        fallback_tool_call_id = self._tool_call_ids_by_index.get(index)
        if fallback_tool_call_id:
            return fallback_tool_call_id

        generated_tool_call_id = f"{self._current_assistant_turn_id()}-tool-{index}"
        self._tool_call_ids_by_index[index] = generated_tool_call_id
        return generated_tool_call_id

    def _on_user_msg_enqueued(self, *, frontend_msg_id: str) -> None:
        self._emit_from_thread(
            {
                "type": "user.turn.enqueued",
                "userTurnId": frontend_msg_id,
            }
        )

    def _on_queued_user_msg_committed(self, *, frontend_msg_id: str) -> None:
        self._emit_from_thread(
            {
                "type": "user.turn.committed",
                "userTurnId": frontend_msg_id,
            }
        )

    def _on_ai_content_delta(self, *, content_delta: str) -> None:
        self._emit_from_thread(
            {
                "type": "assistant.content.delta",
                "assistantTurnId": self._current_assistant_turn_id(),
                "delta": content_delta,
            }
        )

    def _on_ai_reasoning_delta(self, *, reasoning_delta: str) -> None:
        self._emit_from_thread(
            {
                "type": "assistant.reasoning.delta",
                "assistantTurnId": self._current_assistant_turn_id(),
                "delta": reasoning_delta,
            }
        )

    def _on_ai_tool_call_started(
        self,
        *,
        index: int,
        tool_call_id: str | None,
        tool_name: str | None,
    ) -> None:
        resolved_tool_call_id = self._resolve_tool_call_id(
            index=index,
            tool_call_id=tool_call_id,
        )
        self._emit_from_thread(
            {
                "type": "assistant.tool.started",
                "assistantTurnId": self._current_assistant_turn_id(),
                "toolCallId": resolved_tool_call_id,
                "toolName": tool_name or "未命名工具",
                "index": index,
            }
        )

    def _on_ai_tool_call_arguments_delta(
        self,
        *,
        index: int,
        tool_call_id: str | None,
        tool_name: str | None,
        arguments_delta: str,
        arguments: str,
    ) -> None:
        del tool_name
        resolved_tool_call_id = self._resolve_tool_call_id(
            index=index,
            tool_call_id=tool_call_id,
        )
        self._emit_from_thread(
            {
                "type": "assistant.tool.arguments.delta",
                "assistantTurnId": self._current_assistant_turn_id(),
                "toolCallId": resolved_tool_call_id,
                "delta": arguments_delta,
                "arguments": arguments,
            }
        )

    def _on_ai_tool_call_finished(
        self,
        *,
        index: int,
        tool_call_id: str | None,
        tool_name: str | None,
        arguments: str,
    ) -> None:
        del tool_name
        resolved_tool_call_id = self._resolve_tool_call_id(
            index=index,
            tool_call_id=tool_call_id,
        )
        self._emit_from_thread(
            {
                "type": "assistant.tool.completed",
                "assistantTurnId": self._current_assistant_turn_id(),
                "toolCallId": resolved_tool_call_id,
                "arguments": arguments,
            }
        )

    def _on_tool_result(
        self,
        *,
        tool_call_id: str | None,
        result_json_str: str,
    ) -> None:
        resolved_tool_call_id = tool_call_id or f"{self._current_assistant_turn_id()}-tool-0"
        self._emit_from_thread(
            {
                "type": "tool.result",
                "assistantTurnId": self._current_assistant_turn_id(),
                "toolCallId": resolved_tool_call_id,
                "result": result_json_str,
            }
        )


async def healthcheck(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def websocket_sender_loop(websocket: WebSocket, session: ChatSession) -> None:
    while True:
        event = await session.next_event()
        if event is None:
            return
        await websocket.send_json(event)


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_running_loop()
    try:
        session = ChatSession(loop=loop)
    except Exception as exc:
        await websocket.send_json(
            {
                "type": "error",
                "code": "session_init_failed",
                "message": str(exc),
            }
        )
        await websocket.close()
        return

    sender_task = asyncio.create_task(websocket_sender_loop(websocket, session))
    await session.send_session_started()

    try:
        while True:
            payload_text = await websocket.receive_text()
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "invalid_json",
                        "message": "客户端发送的消息不是合法 JSON。",
                    }
                )
                continue

            try:
                command = parse_client_command(payload)
            except (ValidationError, ValueError) as exc:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "invalid_command",
                        "message": str(exc),
                    }
                )
                continue

            if isinstance(command, PingCommand):
                continue

            session.enqueue_user_message(
                user_turn_id=command.userTurnId,
                content=command.content,
            )
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        sender_task.cancel()
        with suppress(asyncio.CancelledError):
            await sender_task


def build_app() -> Starlette:
    routes = [
        Route("/healthz", endpoint=healthcheck),
        WebSocketRoute("/ws", endpoint=websocket_endpoint),
    ]
    return Starlette(routes=routes)
