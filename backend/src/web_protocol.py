from typing import Any, Literal

from pydantic import BaseModel


class SendUserMessageCommand(BaseModel):
    type: Literal["send_user_message"]
    userMessageId: str
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
