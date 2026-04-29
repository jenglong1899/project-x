import asyncio
import json
import logging
from contextlib import suppress

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.websocket_chat_session import WebSocketChatSession
from src.web_protocol import PingCommand, SendUserMessageCommand, parse_client_command


logger = logging.getLogger(__name__)


async def healthcheck(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def send_error(websocket: WebSocket, *, code: str, message: str) -> None:
    await websocket.send_json(
        {
            "type": "error",
            "code": code,
            "message": message,
        }
    )


async def websocket_sender_loop(websocket: WebSocket, session: WebSocketChatSession) -> None:
    while True:
        event = await session.next_event()
        if event is None:
            return
        await websocket.send_json(event)


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        session = WebSocketChatSession()
    except Exception as exc:
        await send_error(
            websocket,
            code="session_init_failed",
            message=str(exc),
        )
        await websocket.close()
        return

    sender_task = asyncio.create_task(websocket_sender_loop(websocket, session))

    try:
        while True:
            payload_text = await websocket.receive_text()
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                await send_error(
                    websocket,
                    code="invalid_json",
                    message="客户端发送的消息不是合法 JSON。",
                )
                continue

            try:
                command = parse_client_command(payload)
            except (ValidationError, ValueError) as exc:
                await send_error(
                    websocket,
                    code="invalid_command",
                    message=str(exc),
                )
                continue

            match command:
                case PingCommand():
                    continue
                case SendUserMessageCommand(userMessageId=user_message_id, content=content):
                    await session.submit_user_message(
                        user_message_id=user_message_id,
                        content=content,
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
