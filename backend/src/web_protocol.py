from typing import Any, Literal

from pydantic import BaseModel


class SendUserMessageCommand(BaseModel):
    type: Literal["send_user_message"]
    userMessageId: str
    content: str


class PingCommand(BaseModel):
    type: Literal["ping"]


class RequestPauseCommand(BaseModel):
    type: Literal["request_pause"]


class ResumeCommand(BaseModel):
    type: Literal["resume"]


ClientCommand = SendUserMessageCommand | PingCommand | RequestPauseCommand | ResumeCommand


def parse_client_command(payload: dict[str, Any]) -> ClientCommand:
    command_type = payload.get("type")
    if command_type == "send_user_message":
        return SendUserMessageCommand.model_validate(payload)
    if command_type == "ping":
        return PingCommand.model_validate(payload)
    if command_type == "request_pause":
        return RequestPauseCommand.model_validate(payload)
    if command_type == "resume":
        return ResumeCommand.model_validate(payload)
    raise ValueError(f"不支持的 command.type: {command_type}")
