import json
import os
import logging
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, AsyncIterator, Callable, Optional

import httpx

from src.core.codex_auth import CodexTokens, resolve_codex_tokens
from src.core.agent_turn import Tool

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexRuntime:
    base_url: str
    access_token: str


def resolve_codex_runtime(*, base_url_override: str | None = None) -> CodexRuntime:
    """
    只负责把“能发请求的 runtime 参数”组装出来。
    网络协议/流式解析后续放到 CodexClient 里做，避免 auth 与协议耦合。
    """
    tokens: CodexTokens = resolve_codex_tokens(import_from_cli_if_missing=True)
    base_url = ""
    if base_url_override is not None:
        base_url = str(base_url_override).strip().rstrip("/")
    if not base_url:
        base_url = os.getenv("PROJECT_X_CODEX_BASE_URL", DEFAULT_CODEX_BASE_URL).strip().rstrip("/")
    if not base_url:
        base_url = DEFAULT_CODEX_BASE_URL
    return CodexRuntime(base_url=base_url, access_token=tokens.access_token)


class CodexClient:
    def __init__(self, *, base_url: str | None = None) -> None:
        self._runtime = resolve_codex_runtime(base_url_override=base_url)

    @staticmethod
    def _http_timeout_s() -> float:
        """
        默认超时偏保守（更快失败），但真实 API/集成测试在网络不稳定时会更容易抖动。

        优先级（从高到低）：
        - PROJECT_X_CODEX_HTTP_TIMEOUT_S：明确只影响 Codex HTTP
        - PROJECT_X_INTEGRATION_TIMEOUT_S：给集成测试统一调参用
        - 60 秒默认值
        """
        for env_key in ("PROJECT_X_CODEX_HTTP_TIMEOUT_S", "PROJECT_X_INTEGRATION_TIMEOUT_S"):
            raw = os.getenv(env_key, "").strip()
            if not raw:
                continue
            try:
                timeout_s = float(raw)
            except Exception:
                raise ValueError(f"{env_key} 必须是数字，但拿到的是：{raw!r}")
            if timeout_s <= 0:
                raise ValueError(f"{env_key} 必须 > 0，但拿到的是：{raw!r}")
            return timeout_s
        return 60.0

    @staticmethod
    def _deterministic_call_id(*, tool_name: str, arguments: str, index: int) -> str:
        payload = f"{tool_name}\n{arguments}\n{index}".encode("utf-8", errors="replace")
        digest = sha256(payload).hexdigest()[:16]
        return f"call_{digest}"

    @staticmethod
    def _responses_tools(tools: list[Tool]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tool in tools:
            out.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_json_schema,
                }
            )
        return out

    @staticmethod
    def _chat_messages_to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue
            if role in {"user", "assistant"}:
                if role == "assistant":
                    tool_calls = msg.get("tool_calls") or []
                    if isinstance(tool_calls, list) and tool_calls:
                        for i, tc in enumerate(tool_calls):
                            if not isinstance(tc, dict):
                                continue
                            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                            name = str(fn.get("name") or "").strip()
                            arguments = fn.get("arguments")
                            if arguments is None:
                                arguments = ""
                            if not isinstance(arguments, str):
                                arguments = json.dumps(arguments, ensure_ascii=False)
                            call_id = str(tc.get("id") or "").strip()
                            if not call_id:
                                call_id = CodexClient._deterministic_call_id(
                                    tool_name=name, arguments=arguments, index=i
                                )
                            items.append(
                                {
                                    "type": "function_call",
                                    "call_id": call_id,
                                    "name": name,
                                    "arguments": arguments,
                                }
                            )

                text = msg.get("content")
                if text is None:
                    text = ""
                text = str(text)
                if text:
                    items.append(
                        {
                            "type": "message",
                            "role": role,
                            "content": [
                                {
                                    "type": "input_text" if role == "user" else "output_text",
                                    "text": text,
                                }
                            ],
                        }
                    )
                continue
            if role == "tool":
                call_id = str(msg.get("tool_call_id") or "").strip()
                output = msg.get("content")
                if output is None:
                    output = ""
                if not call_id:
                    raise ValueError("Codex 输入需要 tool_call_id（role=tool）。")
                items.append({"type": "function_call_output", "call_id": call_id, "output": str(output)})
                continue
        return items

    @staticmethod
    async def _sse_events(*, response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
        """
        解析 `text/event-stream` 的 data 帧，产出单条 JSON dict。

        注意：服务端可能使用 CRLF（`\\r\\n`），不能用简单的 `\\n\\n` 去切分帧，
        否则会出现“请求一直有数据但永远解析不出事件”的假卡死。
        """
        buffer = ""
        data_lines: list[str] = []

        def _flush_event() -> dict[str, Any] | None:
            if not data_lines:
                return None
            data = "\n".join(data_lines).strip()
            data_lines.clear()
            if not data or data == "[DONE]":
                return None
            try:
                payload = json.loads(data)
            except Exception:
                return None
            return payload if isinstance(payload, dict) else None

        async for chunk in response.aiter_text():
            if not chunk:
                continue
            buffer += chunk

            while True:
                newline_index = buffer.find("\n")
                if newline_index < 0:
                    break
                raw_line = buffer[:newline_index]
                buffer = buffer[newline_index + 1:]
                line = raw_line.rstrip("\r")

                # 空行表示一个 SSE event 结束
                if not line:
                    payload = _flush_event()
                    if payload is not None:
                        yield payload
                    continue

                if line.startswith("data:"):
                    data_lines.append(line[len("data:"):].lstrip())

        payload = _flush_event()
        if payload is not None:
            yield payload

    async def stream_assistant_message(
            self,
            *,
            model: str,
            messages: list[dict[str, Any]],
            tools: list[Tool],
            on_text_delta: Callable[[str], None],
            on_reasoning_delta: Callable[[str], None],
            on_tool_call_delta: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        instructions = ""
        payload_messages = messages
        if messages and messages[0].get("role") == "system":
            instructions = str(messages[0].get("content") or "").strip()
            payload_messages = messages[1:]

        body = {
            "model": model,
            "instructions": instructions,
            "input": self._chat_messages_to_responses_input(payload_messages),
            "tools": self._responses_tools(tools),
            "tool_choice": "auto" if tools else None,
            "parallel_tool_calls": True if tools else None,
            "store": False,
            "stream": True,
        }
        body = {k: v for k, v in body.items() if v is not None}

        headers = {
            "Authorization": f"Bearer {self._runtime.access_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        url = f"{self._runtime.base_url}/responses"

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        output_items: list[dict[str, Any]] = []
        tool_call_index_by_id: dict[str, int] = {}
        next_tool_call_index = 0

        async with httpx.AsyncClient(timeout=httpx.Timeout(self._http_timeout_s())) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in {401, 403}:
                        raise RuntimeError(
                            "Codex OAuth 凭据可能已过期或无权限（401/403）。"
                            "请先确保 Codex CLI 能正常登录并生成 ~/.codex/auth.json，"
                            "然后删除 ~/.project-x/auth.json 让 project-x 重新导入。"
                        ) from exc
                    raise
                async for event in self._sse_events(response=resp):
                    event_type = str(event.get("type") or "")
                    if event_type == "response.output_text.delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            content_parts.append(delta)
                            on_text_delta(delta)
                    elif "reasoning" in event_type and "delta" in event_type:
                        delta = str(event.get("delta") or "")
                        if delta:
                            reasoning_parts.append(delta)
                            on_reasoning_delta(delta)
                    elif on_tool_call_delta and (
                            event_type == "response.function_call_arguments.delta"
                            or event_type == "response.custom_tool_call_input.delta"
                    ):
                        delta = str(event.get("delta") or "")
                        if not delta:
                            continue
                        call_id = str(event.get("call_id") or "").strip()
                        item_id = str(event.get("item_id") or event.get("id") or "").strip()

                        # 重要：Codex Responses 的流式事件里，某些 delta 只带 item_id（或只带 call_id），
                        # 但最终 output_item 可能只带另一种 ID。如果这里在不同阶段选择不同的 ID，
                        # 就会导致同一个 tool call 在前端出现两张卡片（一个“未命名工具”且永远等不到 result）。
                        #
                        # 目前我们选择“尽量用 item_id”：它在流式 delta 中更稳定/更常见，
                        # 并且 output_item.done 里也一定会带 id（即 item_id）。
                        stable_id = item_id or call_id
                        if not stable_id:
                            continue
                        if call_id and item_id and call_id != item_id:
                            logger.debug(
                                "Codex tool call id 不一致：优先使用 item_id 作为 tool_call_id（避免前端重复卡片）",
                                extra={
                                    "event_type": event_type,
                                    "call_id": call_id,
                                    "item_id": item_id,
                                    "chosen_id": stable_id,
                                },
                            )
                        index = tool_call_index_by_id.get(stable_id)
                        if index is None:
                            index = next_tool_call_index
                            tool_call_index_by_id[stable_id] = index
                            next_tool_call_index += 1

                        name = str(event.get("name") or event.get("tool_name") or "").strip()
                        tool_call_delta: dict[str, Any] = {
                            "index": index,
                            "id": stable_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": delta,
                            },
                        }
                        on_tool_call_delta(tool_call_delta)
                    elif event_type == "response.output_item.done":
                        item = event.get("item")
                        if isinstance(item, dict):
                            output_items.append(item)
                    elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
                        response_obj = event.get("response")
                        if isinstance(response_obj, dict):
                            output = response_obj.get("output")
                            if isinstance(output, list) and output:
                                output_items = [o for o in output if isinstance(o, dict)]
                        break

        tool_calls: list[dict[str, Any]] = []
        for item in output_items:
            item_type = item.get("type")
            if item_type not in {"function_call", "custom_tool_call"}:
                continue
            call_id = str(item.get("call_id") or "").strip()
            item_id = str(item.get("id") or "").strip()
            tool_calls.append(
                {
                    # 与流式阶段保持一致：优先用 item_id 作为 tool_call_id，避免前端出现重复工具卡片。
                    "id": item_id or call_id,
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or item.get("input") or ""),
                    },
                }
            )

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts) or None}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        if reasoning_parts:
            assistant_msg["reasoning_content"] = "".join(reasoning_parts)
        return assistant_msg
