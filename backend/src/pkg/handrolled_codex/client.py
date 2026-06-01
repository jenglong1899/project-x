import json
import os
import logging
import asyncio
import base64
import binascii
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, AsyncIterator, Callable, Optional

import httpx

from src.pkg.handrolled_codex.auth import CodexTokens, resolve_codex_tokens
from src.tools.tool import Tool

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
logger = logging.getLogger(__name__)


class CodexSseError(RuntimeError):
    def __init__(
        self,
        *,
        message: str,
        code: str | None = None,
        param: str | None = None,
        url: str | None = None,
        model: str | None = None,
        raw_event: dict[str, Any] | None = None,
    ) -> None:
        parts: list[str] = ["Codex SSE error event"]
        if url:
            parts.append(f"- url={url!r}")
        if model:
            parts.append(f"- model={model!r}")
        parts.append(f"- message={message!r}")
        if code:
            parts.append(f"- code={code!r}")
        if param:
            parts.append(f"- param={param!r}")
        super().__init__("\n".join(parts) + "\n")
        self.code = code
        self.param = param
        self.url = url
        self.model = model
        self.raw_event = raw_event

    @staticmethod
    def _get_field(obj: Any, key: str) -> Any:
        return obj.get(key) if isinstance(obj, dict) else None

    @classmethod
    def from_event(
        cls,
        *,
        event: dict[str, Any],
        url: str,
        model: str,
    ) -> "CodexSseError":
        # 经验：不同后端/版本里 error 事件的字段形状并不稳定，尽量宽松提取。
        nested_err = cls._get_field(event, "error")
        message = (
            cls._get_field(event, "message")
            or cls._get_field(nested_err, "message")
            or cls._get_field(event, "detail")
            or cls._get_field(nested_err, "detail")
            or "stream emitted error event"
        )
        code = cls._get_field(event, "code") or cls._get_field(nested_err, "code")
        param = cls._get_field(event, "param") or cls._get_field(nested_err, "param")
        return cls(
            message=str(message).strip(),
            code=str(code).strip() if isinstance(code, str) and code.strip() else None,
            param=str(param).strip() if isinstance(param, str) and param.strip() else None,
            url=url,
            model=model,
            raw_event=event,
        )


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
    def _parse_env_bool(value: str) -> bool:
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        raise ValueError(f"布尔环境变量仅支持 1/0 true/false yes/no on/off，但拿到的是：{value!r}")

    @staticmethod
    def _http_trust_env() -> bool:
        """
        是否让 httpx 从环境变量读取代理/证书配置（HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY 等）。

        默认 True（沿用 httpx 默认行为）。但在某些环境里，代理变量会被自动注入且不可用，
        会导致出现 `RemoteProtocolError: Server disconnected without sending a response` 等难定位问题。
        此时可设置 `PROJECT_X_CODEX_HTTP_TRUST_ENV=0` 来禁用环境代理。
        """
        raw = os.getenv("PROJECT_X_CODEX_HTTP_TRUST_ENV", "").strip()
        if not raw:
            return True
        return CodexClient._parse_env_bool(raw)

    @staticmethod
    def _http_proxy_override() -> str | None:
        """
        显式指定 Codex HTTP 代理（优先级高于环境变量代理）。

        用法示例：
        - `PROJECT_X_CODEX_HTTP_PROXY=socks5h://127.0.0.1:7890`
        - `PROJECT_X_CODEX_HTTP_PROXY=http://127.0.0.1:7890`
        """
        raw = os.getenv("PROJECT_X_CODEX_HTTP_PROXY", "").strip()
        return raw or None

    @staticmethod
    def _proxy_env_snapshot() -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            value = os.getenv(key, "")
            if value:
                out[key] = value
        return out

    @staticmethod
    def _looks_like_unexpanded_shell_var(value: str) -> bool:
        """
        一些 IDE（例如 PyCharm 的 Run Configuration 里引用 env 文件）只是“按行解析键值”，
        并不会像 shell 那样展开 `$http_proxy` 这类引用，导致代理值变成字面量 `$http_proxy`，
        httpx 解析 proxy 时会直接失败并快速报 ConnectError。
        """
        raw = str(value or "").strip()
        if not raw.startswith("$"):
            return False
        name = raw[1:]
        if not name:
            return False
        return all(ch.isalnum() or ch == "_" for ch in name)

    @staticmethod
    def _http_max_retries() -> int:
        """
        Codex 的网络抖动（尤其是 ReadTimeout）会导致一次 run() 直接失败，
        进而出现“工具执行完了但后续 assistant 没有回复”的体验。

        这里的重试只覆盖“完全没有任何流式事件产出”的场景：
        - 如果已经开始有 delta（文本/推理/工具参数）了，再重试会造成前端内容重复，得不偿失。
        """
        raw = os.getenv("PROJECT_X_CODEX_HTTP_MAX_RETRIES", "").strip()
        if not raw:
            return 5
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"PROJECT_X_CODEX_HTTP_MAX_RETRIES 必须是整数，但拿到的是：{raw!r}")
        if value < 0:
            raise ValueError(f"PROJECT_X_CODEX_HTTP_MAX_RETRIES 必须 >= 0，但拿到的是：{raw!r}")
        return value

    @staticmethod
    def _http_retry_backoff_s() -> float:
        raw = os.getenv("PROJECT_X_CODEX_HTTP_RETRY_BACKOFF_S", "").strip()
        if not raw:
            return 0.5
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"PROJECT_X_CODEX_HTTP_RETRY_BACKOFF_S 必须是数字，但拿到的是：{raw!r}")
        if value < 0:
            raise ValueError(f"PROJECT_X_CODEX_HTTP_RETRY_BACKOFF_S 必须 >= 0，但拿到的是：{raw!r}")
        return value

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
            except (TypeError, ValueError):
                raise ValueError(f"{env_key} 必须是数字，但拿到的是：{raw!r}")
            if timeout_s <= 0:
                raise ValueError(f"{env_key} 必须 > 0，但拿到的是：{raw!r}")
            return timeout_s
        return 60.0

    @staticmethod
    def _codex_cloudflare_headers(*, access_token: str) -> dict[str, str]:
        """
        chatgpt.com/backend-api/codex 前面有 Cloudflare。某些网络环境下如果缺少“白名单 originator”类 header，
        会直接 403（即使 token 正确）。

        这里尽量模仿 codex CLI 的指纹：
        - originator: codex_cli_rs
        - User-Agent: codex_cli_rs/... (Project X)
        - ChatGPT-Account-ID: 从 OAuth JWT claim 里解析（解析失败则忽略）
        """
        headers: dict[str, str] = {
            "originator": "codex_cli_rs",
            "User-Agent": "codex_cli_rs/0.0.0 (Project X)",
        }

        token = str(access_token or "").strip()
        if not token:
            return headers

        try:
            parts = token.split(".")
            if len(parts) < 2:
                return headers
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8", errors="ignore")))
            acct_id = (
                claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
                if isinstance(claims, dict)
                else None
            )
            if isinstance(acct_id, str) and acct_id.strip():
                headers["ChatGPT-Account-ID"] = acct_id.strip()
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return headers

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
            except json.JSONDecodeError:
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

        reasoning = {"effort": "medium", "summary": "auto"}
        include = ["reasoning.encrypted_content"]
        body = {
            "model": model,
            "instructions": instructions,
            "input": self._chat_messages_to_responses_input(payload_messages),
            "tools": self._responses_tools(tools),
            "tool_choice": "auto" if tools else None,
            "parallel_tool_calls": True if tools else None,
            "store": False,
            "stream": True,
            "reasoning": reasoning,
            "include": include,
        }
        body = {k: v for k, v in body.items() if v is not None}

        headers = {
            "Authorization": f"Bearer {self._runtime.access_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        headers.update(self._codex_cloudflare_headers(access_token=self._runtime.access_token))
        url = f"{self._runtime.base_url}/responses"

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        output_items: list[dict[str, Any]] = []
        tool_call_index_by_id: dict[str, int] = {}
        next_tool_call_index = 0

        max_retries = self._http_max_retries()
        base_backoff_s = self._http_retry_backoff_s()
        timeout_s = self._http_timeout_s()
        proxy_override = self._http_proxy_override()
        trust_env = self._http_trust_env()
        if proxy_override is not None:
            # 显式指定 proxy 时，为避免“环境代理叠加/覆盖”造成行为不确定，这里强制关闭 trust_env。
            trust_env = False

        for attempt in range(max_retries + 1):
            received_any_event = False
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout_s),
                    proxy=proxy_override,
                    trust_env=trust_env,
                ) as client:
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
                            received_any_event = True
                            event_type = str(event.get("type") or "")
                            if event_type == "error":
                                raise CodexSseError.from_event(event=event, url=url, model=model)
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
                                    logger.info(
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
                break
            except (
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.ProxyError,
            ) as exc:
                # 如果已经收到过任何 SSE 事件，再重试会导致前端内容重复，因此直接把原异常抛出去。
                if received_any_event:
                    raise

                should_retry = attempt < max_retries
                if not should_retry:
                    proxy_env = self._proxy_env_snapshot()
                    hint_lines: list[str] = []
                    if proxy_override is not None:
                        hint_lines.append(f"- 当前使用 PROJECT_X_CODEX_HTTP_PROXY={proxy_override!r}")
                    else:
                        if proxy_env:
                            hint_lines.append(f"- 当前进程检测到代理环境变量：{proxy_env!r}")
                            if any(self._looks_like_unexpanded_shell_var(v) for v in proxy_env.values()):
                                hint_lines.append(
                                    "- 检测到代理值疑似未展开的 shell 变量（例如字面量 '$http_proxy'）。"
                                    "如果你在 IDE 里引用 ~/.zshenv/.bashrc 作为 env 文件，通常不会展开 $VAR；"
                                    "请在 Run/Debug Configuration 里填入完整代理 URL，"
                                    "或改用 PROJECT_X_CODEX_HTTP_PROXY 显式指定。"
                                )
                            hint_lines.append("- 可尝试设置 PROJECT_X_CODEX_HTTP_TRUST_ENV=0 禁用环境代理")
                            hint_lines.append("- 或设置 PROJECT_X_CODEX_HTTP_PROXY 显式指定可用代理（如 socks5h://...）")
                    hint = "\n".join(hint_lines)
                    message = (
                        "Codex 网络请求失败（未收到任何流式事件）。\n"
                        f"- url={url!r}\n"
                        f"- error_type={type(exc).__name__}\n"
                        f"- trust_env={trust_env}\n"
                    )
                    if hint:
                        message += hint + "\n"
                    raise RuntimeError(message) from exc
                backoff_s = min(base_backoff_s * (2 ** attempt), 5.0)
                logger.warning(
                    "Codex 请求超时/连接失败，准备重试",
                    extra={
                        "attempt": attempt + 1,
                        "max_attempts": max_retries + 1,
                        "backoff_s": backoff_s,
                        "error_type": type(exc).__name__,
                        "timeout_s": timeout_s,
                        "trust_env": trust_env,
                        "proxy_override": proxy_override,
                        "proxy_env": self._proxy_env_snapshot() if proxy_override is None else None,
                    },
                )
                if backoff_s > 0:
                    await asyncio.sleep(backoff_s)

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
