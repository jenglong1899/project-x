import os
import time
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias
from litellm import completion

from src.core.model_config import ModelConfig


class OnAiContentDelta(Protocol):
    def __call__(self, *, content_delta: str) -> None: ...


class OnAiReasoningDelta(Protocol):
    def __call__(self, *, reasoning_delta: str) -> None: ...


class OnAiToolCallStarted(Protocol):
    def __call__(
            self,
            *,
            index: int,
            tool_call_id: str | None,
            tool_name: str | None,
    ) -> None: ...


class OnAiToolCallArgumentsDelta(Protocol):
    def __call__(
            self,
            *,
            index: int,
            tool_call_id: str | None,
            tool_name: str | None,
            arguments_delta: str,
    ) -> None: ...


class OnAiToolCallFinished(Protocol):
    def __call__(
            self,
            *,
            index: int,
            tool_call_id: str | None,
            tool_name: str | None,
            arguments: str,
    ) -> None: ...


class ToolHandler(Protocol):
    def __call__(self, *, arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    handler: ToolHandler

    def to_tool_param(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_json_schema,
            },
        }


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
                                  on_ai_tool_call_started: OnAiToolCallStarted) -> None:
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
        index=index,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )


def stream(*, model_config: ModelConfig,
           messages: list[dict[str, Any]],
           tools: list[ToolSpec],
           on_ai_content_delta: OnAiContentDelta,
           on_ai_reasoning_delta: OnAiReasoningDelta,
           on_ai_tool_call_started: OnAiToolCallStarted,
           on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta,
           on_ai_tool_call_finished: OnAiToolCallFinished) -> dict[str, Any]:
    if model_config.model == "mock":
        delay_ms_text = os.getenv("PROJECT_X_MOCK_MODEL_DELAY_MS", "0").strip()
        try:
            delay_ms = int(delay_ms_text)
        except ValueError:
            delay_ms = 0

        if delay_ms > 0:
            time.sleep(delay_ms / 1000)

        content = "（mock 回复）"
        on_ai_content_delta(content_delta=content)
        return {
            "role": "assistant",
            "content": content,
        }

    completion_kwargs: dict[str, Any] = {
        "model": model_config.model,
        "tools": [tool.to_tool_param() for tool in tools],
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
            on_ai_content_delta(content_delta=content_delta)

        reasoning_delta = _normalize_text_chunk(
            _get_field(delta, "reasoning_content", _get_field(delta, "reasoning"))
        )
        if reasoning_delta:
            reasoning_parts.append(reasoning_delta)
            on_ai_reasoning_delta(reasoning_delta=reasoning_delta)

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
                    index=tool_call_index,
                    tool_call_id=tool_call["id"] or None,
                    tool_name=tool_call["function"]["name"] or None,
                    arguments_delta=arguments_delta,
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
                index=index,
                tool_call_id=tool_call["id"] or None,
                tool_name=tool_call["function"]["name"] or None,
                arguments=tool_call["function"]["arguments"],
            )
        if not assistant_message["content"]:
            assistant_message["content"] = None

    if reasoning_parts:
        assistant_message["reasoning_content"] = "".join(reasoning_parts)

    return assistant_message


@dataclass
class ResetContextDirective:
    prompt_to_my_future_self: str


# 做这个只是为了对称，实际上目前run()只会判断是不是ResetContextDirective
# 不会判断是不是ContinueLoopDirective，
# 也就是说run()是默认"只要不是reset context，那就是continue
@dataclass
class ContinueLoopDirective:
    pass


OrchestratorDirective: TypeAlias = ContinueLoopDirective | ResetContextDirective


@dataclass(frozen=True)
class ToolExecutionOutcome:
    tool_messages: list[dict[str, Any]]
    directive: OrchestratorDirective


class OnToolResult(Protocol):
    def __call__(self, *,
                 tool_call_id: str | None,
                 result_json_str: str) -> None: ...


def _parse_tool_arguments(*, tool_name: str, arguments: str) -> dict[str, Any]:
    if not arguments:
        return {}

    parsed_arguments = json.loads(arguments)
    if not isinstance(parsed_arguments, dict):
        raise ValueError(f"tool {tool_name} arguments 必须是 JSON object")
    return parsed_arguments


def _stringify_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def execute_tool_calls(*, ai_msg_dict: dict[str, Any],
                       tools_by_name: Mapping[str, ToolSpec],
                       on_tool_result: OnToolResult) -> ToolExecutionOutcome:
    tool_messages: list[dict[str, Any]] = []
    for index, tool_call in enumerate(ai_msg_dict.get("tool_calls", [])):
        function_payload = _get_field(tool_call, "function", {})
        tool_name = _get_field(function_payload, "name")
        if not tool_name:
            raise ValueError("tool call 缺少 function.name")

        tool_spec = tools_by_name.get(tool_name)
        if tool_spec is None:
            raise ValueError(f"未注册的工具: {tool_name}")

        arguments = _get_field(function_payload, "arguments", "") or ""
        parsed_arguments = _parse_tool_arguments(tool_name=tool_name, arguments=arguments)
        tool_result = tool_spec.handler(arguments=parsed_arguments)

        result_json_str = _stringify_tool_result(tool_result)
        tool_call_id = _get_field(tool_call, "id")
        tool_msg: dict[str, Any] = {"role": "tool", "content": result_json_str, "tool_call_id": tool_call_id}
        tool_messages.append(tool_msg)

        on_tool_result(
            tool_call_id=tool_call_id,
            result_json_str=result_json_str,
        )

    return ToolExecutionOutcome(
        tool_messages=tool_messages,
        directive=ContinueLoopDirective(),
    )
