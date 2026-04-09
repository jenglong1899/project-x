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
from src.commons import ORIGINALS_DIR
from src.conversation_store import ConversationStore
from src.web_protocol import PingCommand, SendUserMessageCommand, parse_client_command


logger = logging.getLogger(__name__)


async def healthcheck(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})

async def list_conversations(_: Request) -> JSONResponse:
    originals_dir = ORIGINALS_DIR.expanduser()
    if not originals_dir.exists():
        return JSONResponse({"conversations": []})

    conversations: list[dict[str, object]] = []
    for path in sorted(originals_dir.glob("*.json")):
        if not path.is_file():
            continue
        conversation_id = path.name
        try:
            store = ConversationStore.load_from_conversation_id(
                conversation_id=conversation_id,
                originals_dir=originals_dir,
            )
        except Exception:
            logger.exception("加载 conversation 失败，已跳过: %s", conversation_id)
            continue

        conversations.append(
            {
                "conversationId": store.conversation_id,
                "displayName": store.display_name,
                "lastChatTime": store.last_chat_time,
            }
        )

    conversations.sort(
        key=lambda item: (
            str(item.get("lastChatTime") or ""),
            str(item.get("conversationId") or ""),
        ),
        reverse=True,
    )
    return JSONResponse({"conversations": conversations})


async def get_conversation(request: Request) -> JSONResponse:
    conversation_id = str(request.path_params.get("conversationId") or "")
    try:
        store = ConversationStore.load_from_conversation_id(conversation_id=conversation_id)
    except FileNotFoundError as exc:
        return JSONResponse(
            {
                "code": "conversation_not_found",
                "message": str(exc),
            },
            status_code=404,
        )
    except ValueError as exc:
        return JSONResponse(
            {
                "code": "invalid_conversation_id",
                "message": str(exc),
            },
            status_code=400,
        )

    return JSONResponse(
        {
            "conversationId": store.conversation_id,
            "displayName": store.display_name,
            "lastChatTime": store.last_chat_time,
            "messages": store.build_messages_from_history(),
        }
    )


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
    conversation_id = websocket.query_params.get("conversationId") or None
    try:
        session = WebSocketChatSession(conversation_id=conversation_id)
    except Exception as exc:
        if conversation_id:
            if isinstance(exc, FileNotFoundError):
                code = "conversation_not_found"
            elif isinstance(exc, ValueError):
                code = "invalid_conversation_id"
            else:
                code = "session_init_failed"
        else:
            code = "session_init_failed"

        await send_error(
            websocket,
            code=code,
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
        Route("/conversations", endpoint=list_conversations),
        Route("/conversations/{conversationId:str}", endpoint=get_conversation),
        WebSocketRoute("/ws", endpoint=websocket_endpoint),
    ]
    return Starlette(routes=routes)
