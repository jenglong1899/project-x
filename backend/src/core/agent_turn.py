import asyncio
import os
import json
from collections.abc import AsyncIterable, Sequence
from typing import Any, Protocol, cast
from dataclasses import dataclass
from litellm import acompletion

from src.core.model_config import ModelConfig
from src.tools.tool import Tool, ToolHandler


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


@dataclass(frozen=True)
class TurnUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class TurnResult:
    assistant_message: dict[str, Any]
    usage: TurnUsage


ESCALATE_TO_HUMAN_TOOL_NAME = "escalate_to_human"



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

    function_name_delta = _normalize_text_chunk(_get_field(function_delta, "name"))
    if function_name_delta:
        tool_call["function"]["name"] += function_name_delta

    function_arguments_delta = _normalize_text_chunk(_get_field(function_delta, "arguments"))
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

def _emit_tool_call_arguments_delta_if_any(*,
                                          tool_calls_by_index: dict[int, dict[str, Any]],
                                          index: int,
                                          function_delta: Any,
                                          on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta) -> None:
    arguments_delta = _normalize_text_chunk(
        _get_field(function_delta, "arguments") if function_delta else None
    )
    if not arguments_delta:
        return

    tool_call = tool_calls_by_index[index]
    on_ai_tool_call_arguments_delta(
        index=index,
        tool_call_id=tool_call["id"] or None,
        tool_name=tool_call["function"]["name"] or None,
        arguments_delta=arguments_delta,
    )


def _emit_tool_call_finished_for_indexes(*,
                                         tool_calls_by_index: dict[int, dict[str, Any]],
                                         started_tool_call_indexes: set[int],
                                         on_ai_tool_call_started: OnAiToolCallStarted,
                                         on_ai_tool_call_finished: OnAiToolCallFinished) -> None:
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
            tool_call_id=tool_call.get("id") or None,
            tool_name=_get_field(tool_call.get("function"), "name") or None,
            arguments=_get_field(tool_call.get("function"), "arguments") or "",
        )


def _merge_final_tool_calls_into_indexed_aggregation(*,
                                                     tool_calls_by_index: dict[int, dict[str, Any]],
                                                     final_tool_calls: Any) -> None:
    if not isinstance(final_tool_calls, list) or not final_tool_calls:
        return

    # 注意：流式 delta 里的 tool_call.index 可能与最终 tool_calls 列表顺序不一致。
    # 这里不能用 enumerate(final_tool_calls) 去覆盖 tool_calls_by_index，
    # 否则会把“按 delta index 聚合出来的 tool_call”替换成另一个 tool_call，导致：
    # - tool_call_started 去重误判（按 index）
    # - 前端 tool 卡片顺序与事件不一致
    id_to_index: dict[str, int] = {}
    for idx, existing_tc in tool_calls_by_index.items():
        if isinstance(existing_tc, dict):
            existing_id = existing_tc.get("id")
            if isinstance(existing_id, str) and existing_id:
                id_to_index[existing_id] = idx

    next_index = (max(tool_calls_by_index.keys()) + 1) if tool_calls_by_index else 0
    for i, tc in enumerate(final_tool_calls):
        if not isinstance(tc, dict):
            continue
        tc_id = tc.get("id")

        # 优先用 tool_call_id 对齐：把最终 tool_call 内容回填到“delta 聚合”的同一个 index 上。
        if isinstance(tc_id, str) and tc_id and tc_id in id_to_index:
            tool_calls_by_index[id_to_index[tc_id]] = tc
            continue

        # 没有 id（或没在 delta 中出现）时，尽量使用 enumerate 的 i 作为 index，
        # 但如果该 index 已被其他 tool_call 占用（且 id 不一致），则分配一个新 index，
        # 以避免覆盖导致的事件错配。
        if i not in tool_calls_by_index:
            tool_calls_by_index[i] = tc
        else:
            occupied_tc = tool_calls_by_index[i]
            occupied_id = occupied_tc.get("id") if isinstance(occupied_tc, dict) else None
            if not occupied_id or occupied_id == tc_id:
                tool_calls_by_index[i] = tc
            else:
                while next_index in tool_calls_by_index:
                    next_index += 1
                tool_calls_by_index[next_index] = tc
                next_index += 1


async def _stream_with_codex(*,
                             model_config: ModelConfig,
                             messages: list[dict[str, Any]],
                             tools: list[Tool],
                             on_ai_content_delta: OnAiContentDelta,
                             on_ai_reasoning_delta: OnAiReasoningDelta,
                             on_ai_tool_call_started: OnAiToolCallStarted,
                             on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta,
                             on_ai_tool_call_finished: OnAiToolCallFinished) -> TurnResult:
    from src.pkg.handrolled_codex.client import CodexClient

    client = CodexClient(base_url=model_config.base_url)
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    started_tool_call_indexes: set[int] = set()

    def _on_tool_call_delta(tool_call_delta: dict[str, Any]) -> None:
        index = _get_field(tool_call_delta, "index", 0)
        _merge_tool_call_delta(tool_calls_by_index, tool_call_delta)
        _maybe_emit_tool_call_started(
            started_tool_call_indexes=started_tool_call_indexes,
            tool_calls_by_index=tool_calls_by_index,
            index=index,
            on_ai_tool_call_started=on_ai_tool_call_started,
        )

        function_delta = _get_field(tool_call_delta, "function")
        _emit_tool_call_arguments_delta_if_any(
            tool_calls_by_index=tool_calls_by_index,
            index=index,
            function_delta=function_delta,
            on_ai_tool_call_arguments_delta=on_ai_tool_call_arguments_delta,
        )

    assistant_message = await client.stream_assistant_message(
        model=model_config.model,
        messages=messages,
        tools=tools,
        on_text_delta=lambda delta: on_ai_content_delta(content_delta=delta),
        on_reasoning_delta=lambda delta: on_ai_reasoning_delta(reasoning_delta=delta),
        on_tool_call_delta=_on_tool_call_delta,
    )
    usage = _extract_turn_usage(assistant_message.pop("usage", None))

    _merge_final_tool_calls_into_indexed_aggregation(
        tool_calls_by_index=tool_calls_by_index,
        final_tool_calls=assistant_message.get("tool_calls"),
    )

    _emit_tool_call_finished_for_indexes(
        tool_calls_by_index=tool_calls_by_index,
        started_tool_call_indexes=started_tool_call_indexes,
        on_ai_tool_call_started=on_ai_tool_call_started,
        on_ai_tool_call_finished=on_ai_tool_call_finished,
    )
    return TurnResult(assistant_message=assistant_message, usage=usage)


def _build_litellm_completion_kwargs(*,
                                     model_config: ModelConfig,
                                     messages: list[dict[str, Any]],
                                     tools: list[Tool]) -> dict[str, Any]:
    return {
        "model": model_config.model,
        "tools": [tool.to_tool_param() for tool in tools],
        "messages": messages,
        "api_base": model_config.base_url,
        "api_key": model_config.api_key,
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def _extract_turn_usage(raw_usage: Any) -> TurnUsage:
    if raw_usage is None:
        return TurnUsage()
    return TurnUsage(
        prompt_tokens=_get_field(raw_usage, "prompt_tokens"),
        completion_tokens=_get_field(raw_usage, "completion_tokens"),
        total_tokens=_get_field(raw_usage, "total_tokens"),
    )


def _handle_litellm_delta(*,
                          delta: Any,
                          content_parts: list[str],
                          reasoning_parts: list[str],
                          tool_calls_by_index: dict[int, dict[str, Any]],
                          started_tool_call_indexes: set[int],
                          on_ai_content_delta: OnAiContentDelta,
                          on_ai_reasoning_delta: OnAiReasoningDelta,
                          on_ai_tool_call_started: OnAiToolCallStarted,
                          on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta) -> str | None:
    delta_role = _get_field(delta, "role")

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
        _emit_tool_call_arguments_delta_if_any(
            tool_calls_by_index=tool_calls_by_index,
            index=tool_call_index,
            function_delta=function_delta,
            on_ai_tool_call_arguments_delta=on_ai_tool_call_arguments_delta,
        )

    return delta_role


async def _stream_with_litellm(*,
                               model_config: ModelConfig,
                               messages: list[dict[str, Any]],
                               tools: list[Tool],
                               on_ai_content_delta: OnAiContentDelta,
                               on_ai_reasoning_delta: OnAiReasoningDelta,
                               on_ai_tool_call_started: OnAiToolCallStarted,
                               on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta,
                               on_ai_tool_call_finished: OnAiToolCallFinished) -> TurnResult:
    completion_kwargs = _build_litellm_completion_kwargs(
        model_config=model_config,
        messages=messages,
        tools=tools,
    )

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    started_tool_call_indexes: set[int] = set()
    assistant_role = "assistant"
    usage = TurnUsage()

    response_stream = cast(AsyncIterable[Any], await acompletion(**completion_kwargs))
    async for chunk in response_stream:
        chunk_usage = _get_field(chunk, "usage")
        if chunk_usage is not None:
            usage = _extract_turn_usage(chunk_usage)

        choices = _get_field(chunk, "choices", [])
        if not choices:
            continue

        delta = _get_field(choices[0], "delta")
        if not delta:
            continue

        delta_role = _handle_litellm_delta(
            delta=delta,
            content_parts=content_parts,
            reasoning_parts=reasoning_parts,
            tool_calls_by_index=tool_calls_by_index,
            started_tool_call_indexes=started_tool_call_indexes,
            on_ai_content_delta=on_ai_content_delta,
            on_ai_reasoning_delta=on_ai_reasoning_delta,
            on_ai_tool_call_started=on_ai_tool_call_started,
            on_ai_tool_call_arguments_delta=on_ai_tool_call_arguments_delta,
        )
        if delta_role:
            assistant_role = delta_role

    assistant_message: dict[str, Any] = {
        "role": assistant_role,
        "content": "".join(content_parts),
    }

    if tool_calls_by_index:
        assistant_message["tool_calls"] = [
            tool_calls_by_index[index]
            for index in sorted(tool_calls_by_index)
        ]
        _emit_tool_call_finished_for_indexes(
            tool_calls_by_index=tool_calls_by_index,
            started_tool_call_indexes=started_tool_call_indexes,
            on_ai_tool_call_started=on_ai_tool_call_started,
            on_ai_tool_call_finished=on_ai_tool_call_finished,
        )
        if not assistant_message["content"]:
            assistant_message["content"] = None

    if reasoning_parts:
        assistant_message["reasoning_content"] = "".join(reasoning_parts)

    return TurnResult(assistant_message=assistant_message, usage=usage)


async def stream(*, model_config: ModelConfig,
                 messages: list[dict[str, Any]],
                 tools: list[Tool],
                 on_ai_content_delta: OnAiContentDelta,
                 on_ai_reasoning_delta: OnAiReasoningDelta,
                 on_ai_tool_call_started: OnAiToolCallStarted,
                 on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta,
                 on_ai_tool_call_finished: OnAiToolCallFinished) -> TurnResult:
    if model_config.provider == "openai-codex":
        return await _stream_with_codex(
            model_config=model_config,
            messages=messages,
            tools=tools,
            on_ai_content_delta=on_ai_content_delta,
            on_ai_reasoning_delta=on_ai_reasoning_delta,
            on_ai_tool_call_started=on_ai_tool_call_started,
            on_ai_tool_call_arguments_delta=on_ai_tool_call_arguments_delta,
            on_ai_tool_call_finished=on_ai_tool_call_finished,
        )

    if model_config.provider != "litellm":
        raise ValueError(f"暂不支持的 provider: {model_config.provider}")
    if model_config.model == "mock":
        delay_ms_text = os.getenv("PROJECT_X_MOCK_MODEL_DELAY_MS", "0").strip()
        try:
            delay_ms = int(delay_ms_text)
        except ValueError:
            delay_ms = 0

        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        content = "（mock 回复）"
        on_ai_content_delta(content_delta=content)
        return TurnResult(
            assistant_message={
                "role": "assistant",
                "content": content,
            },
            usage=TurnUsage(),
        )

    return await _stream_with_litellm(
        model_config=model_config,
        messages=messages,
        tools=tools,
        on_ai_content_delta=on_ai_content_delta,
        on_ai_reasoning_delta=on_ai_reasoning_delta,
        on_ai_tool_call_started=on_ai_tool_call_started,
        on_ai_tool_call_arguments_delta=on_ai_tool_call_arguments_delta,
        on_ai_tool_call_finished=on_ai_tool_call_finished,
    )

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


async def execute_tool_calls(*, ai_msg_dict: dict[str, Any],
                             tools: Sequence[Tool],
                             on_tool_result: OnToolResult) -> list[dict[str, Any]]:
    tools_by_name = {tool.name: tool for tool in tools}
    if len(tools_by_name) != len(tools):
        raise ValueError("tools 里存在重复的 name")

    tool_calls = ai_msg_dict.get("tool_calls", [])
    tool_call_names = [
        _get_field(_get_field(tool_call, "function", {}), "name")
        for tool_call in tool_calls
    ]
    if ESCALATE_TO_HUMAN_TOOL_NAME in tool_call_names and len(tool_calls) > 1:
        raise ValueError("escalate_to_human 不能和其他工具一起调用")

    tool_messages: list[dict[str, Any]] = []
    for index, tool_call in enumerate(tool_calls):
        function_payload = _get_field(tool_call, "function", {})
        tool_name = _get_field(function_payload, "name")
        tool_call_id = _get_field(tool_call, "id")
        if not tool_name:
            # 不抛异常（避免把错误上抛成系统错误），而是把错误以 tool result 形式回传给模型，
            # 让模型能在下一条 assistant message 自行纠正。
            result_json_str = json.dumps(
                {
                    "error": "tool call 缺少 function.name",
                    "tool": None,
                    "stage": "run",
                    "index": index,
                },
                ensure_ascii=False,
            )
            tool_messages.append({"role": "tool", "content": result_json_str, "tool_call_id": tool_call_id})
            on_tool_result(tool_call_id=tool_call_id, result_json_str=result_json_str)
            continue

        tool_spec = tools_by_name.get(tool_name)
        if tool_spec is None:
            result_json_str = json.dumps(
                {
                    "error": f"未注册的工具: {tool_name}",
                    "tool": tool_name,
                    "stage": "run",
                    "index": index,
                },
                ensure_ascii=False,
            )
            tool_messages.append({"role": "tool", "content": result_json_str, "tool_call_id": tool_call_id})
            on_tool_result(tool_call_id=tool_call_id, result_json_str=result_json_str)
            continue

        arguments = _get_field(function_payload, "arguments", "") or ""
        try:
            parsed_arguments = _parse_tool_arguments(tool_name=tool_name, arguments=arguments)
        except (json.JSONDecodeError, ValueError) as exc:
            result_json_str = json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "tool": tool_name,
                    "stage": "parse",
                    "index": index,
                },
                ensure_ascii=False,
            )
            tool_messages.append({"role": "tool", "content": result_json_str, "tool_call_id": tool_call_id})
            on_tool_result(tool_call_id=tool_call_id, result_json_str=result_json_str)
            continue

        try:
            tool_result = await tool_spec.handler(arguments=parsed_arguments)
            result_json_str = _stringify_tool_result(tool_result)
        except Exception as exc:
            result_json_str = json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "tool": tool_name,
                    "stage": "run",
                    "index": index,
                },
                ensure_ascii=False,
            )

        tool_messages.append({"role": "tool", "content": result_json_str, "tool_call_id": tool_call_id})
        on_tool_result(tool_call_id=tool_call_id, result_json_str=result_json_str)

    return tool_messages
