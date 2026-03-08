from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias
from litellm import completion

from src.core.model_config import ModelConfig


def _noop(*args: Any, **kwargs: Any) -> None:
    return None


@dataclass(frozen=True)
class ToolCallStartedEvent:
    index: int
    tool_call_id: str | None
    tool_name: str | None


@dataclass(frozen=True)
class ToolCallArgumentsDeltaEvent:
    index: int
    tool_call_id: str | None
    tool_name: str | None
    arguments_delta: str
    arguments: str


@dataclass(frozen=True)
class ToolCallFinishedEvent:
    index: int
    tool_call_id: str | None
    tool_name: str | None
    arguments: str


def _get_field(obj: Any, field_name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(field_name, default)
    return getattr(obj, field_name, default)


def _normalize_text_chunk(chunk: Any) -> str:
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, list):
        parts: list[str] = []
        for item in chunk:
            if isinstance(item, str):
                parts.append(item)
                continue

            text = _get_field(item, "text")
            if text:
                parts.append(str(text))
        return "".join(parts)
    return str(chunk)


def _merge_tool_call_delta(tool_calls_by_index: dict[int, dict[str, Any]], tool_call_delta: Any) -> None:
    index = _get_field(tool_call_delta, "index", 0)
    tool_call = tool_calls_by_index.setdefault(
        index,
        {
            "id": "",
            "type": "function",
            "function": {
                "name": "",
                "arguments": "",
            },
        },
    )

    tool_call_id = _get_field(tool_call_delta, "id")
    if tool_call_id:
        tool_call["id"] = tool_call_id

    tool_call_type = _get_field(tool_call_delta, "type")
    if tool_call_type:
        tool_call["type"] = tool_call_type

    function_delta = _get_field(tool_call_delta, "function")
    if not function_delta:
        return

    function_name_delta = _get_field(function_delta, "name")
    if function_name_delta:
        tool_call["function"]["name"] += function_name_delta

    function_arguments_delta = _get_field(function_delta, "arguments")
    if function_arguments_delta:
        tool_call["function"]["arguments"] += function_arguments_delta


def _maybe_emit_tool_call_started(*,
                                  started_tool_call_indexes: set[int],
                                  tool_calls_by_index: dict[int, dict[str, Any]],
                                  index: int,
                                  on_ai_tool_call_started: Callable[[ToolCallStartedEvent], None]) -> None:
    if index in started_tool_call_indexes:
        return

    tool_call = tool_calls_by_index[index]
    tool_call_id = tool_call["id"] or None
    tool_name = tool_call["function"]["name"] or None
    arguments = tool_call["function"]["arguments"]
    if not tool_call_id and not tool_name and not arguments:
        return

    started_tool_call_indexes.add(index)
    on_ai_tool_call_started(
        ToolCallStartedEvent(
            index=index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
    )


def stream(*, model_config: ModelConfig,
           messages: list[dict[str, Any]],
           tools_params: list[dict[str, Any]],
           on_ai_content_delta: Callable[[str], None],
           on_ai_reasoning_delta: Callable[[str], None],
           on_ai_tool_call_started: Callable[[ToolCallStartedEvent], None] = _noop,
           on_ai_tool_call_arguments_delta: Callable[[ToolCallArgumentsDeltaEvent], None] = _noop,
           on_ai_tool_call_finished: Callable[[ToolCallFinishedEvent], None] = _noop) -> dict[str, Any]:

    completion_kwargs: dict[str, Any] = {
        "model": model_config.model,
        "tools": tools_params,
        "messages": messages,
        "api_base": model_config.base_url,
        "api_key": model_config.api_key,
        "stream": True,
    }

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    started_tool_call_indexes: set[int] = set()
    assistant_role = "assistant"

    response_stream = completion(**completion_kwargs)
    for chunk in response_stream:
        choices = _get_field(chunk, "choices", [])
        if not choices:
            continue

        delta = _get_field(choices[0], "delta")
        if not delta:
            continue

        delta_role = _get_field(delta, "role")
        if delta_role:
            assistant_role = delta_role

        content_delta = _normalize_text_chunk(_get_field(delta, "content"))
        if content_delta:
            content_parts.append(content_delta)
            on_ai_content_delta(content_delta)

        reasoning_delta = _normalize_text_chunk(
            _get_field(delta, "reasoning_content", _get_field(delta, "reasoning"))
        )
        if reasoning_delta:
            reasoning_parts.append(reasoning_delta)
            on_ai_reasoning_delta(reasoning_delta)

        for tool_call_delta in _get_field(delta, "tool_calls", []) or []:
            tool_call_index = _get_field(tool_call_delta, "index", 0)
            _merge_tool_call_delta(tool_calls_by_index, tool_call_delta)
            _maybe_emit_tool_call_started(
                started_tool_call_indexes=started_tool_call_indexes,
                tool_calls_by_index=tool_calls_by_index,
                index=tool_call_index,
                on_ai_tool_call_started=on_ai_tool_call_started,
            )

            function_delta = _get_field(tool_call_delta, "function")
            arguments_delta = _get_field(function_delta, "arguments") if function_delta else None
            if arguments_delta:
                tool_call = tool_calls_by_index[tool_call_index]
                on_ai_tool_call_arguments_delta(
                    ToolCallArgumentsDeltaEvent(
                        index=tool_call_index,
                        tool_call_id=tool_call["id"] or None,
                        tool_name=tool_call["function"]["name"] or None,
                        arguments_delta=arguments_delta,
                        arguments=tool_call["function"]["arguments"],
                    )
                )

    assistant_message: dict[str, Any] = {
        "role": assistant_role,
        "content": "".join(content_parts),
    }

    if tool_calls_by_index:
        assistant_message["tool_calls"] = [
            tool_calls_by_index[index]
            for index in sorted(tool_calls_by_index)
        ]
        for index in sorted(tool_calls_by_index):
            _maybe_emit_tool_call_started(
                started_tool_call_indexes=started_tool_call_indexes,
                tool_calls_by_index=tool_calls_by_index,
                index=index,
                on_ai_tool_call_started=on_ai_tool_call_started,
            )
            tool_call = tool_calls_by_index[index]
            on_ai_tool_call_finished(
                ToolCallFinishedEvent(
                    index=index,
                    tool_call_id=tool_call["id"] or None,
                    tool_name=tool_call["function"]["name"] or None,
                    arguments=tool_call["function"]["arguments"],
                )
            )
        if not assistant_message["content"]:
            assistant_message["content"] = None

    if reasoning_parts:
        assistant_message["reasoning_content"] = "".join(reasoning_parts)

    messages.append(assistant_message)
    return assistant_message


@dataclass
class ResetContextDirective:
    prompt_to_my_future_self: str


# 做这个只是为了对称，实际上目前只会判断是不是ResetContextDirective
# 不会判断是不是ContinueLoopDirective
@dataclass
class ContinueLoopDirective:
    pass


OrchestratorDirective: TypeAlias = ContinueLoopDirective | ResetContextDirective


def execute_tool_and_append() -> OrchestratorDirective:
    raise NotImplementedError
