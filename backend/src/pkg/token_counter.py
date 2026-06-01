from __future__ import annotations

from dataclasses import dataclass
from math import ceil
import os
from pathlib import Path
from typing import Any, cast

try:
    from transformers import AutoTokenizer  # type: ignore
except ImportError:  # pragma: no cover
    AutoTokenizer = None


class TokenCounterError(RuntimeError):
    pass


def _hf_from_pretrained(tokenizer_id: str, **kwargs: Any) -> Any:
    """
    transformers 的类型标注在不同版本/不同环境下可能不一致；这里把返回值统一视为 Any，
    避免静态检查误判 `from_pretrained()` 返回 None。
    """
    if AutoTokenizer is None:  # pragma: no cover
        raise TokenCounterError("未安装 transformers，无法进行精确 token 统计")
    return cast(Any, AutoTokenizer).from_pretrained(tokenizer_id, **kwargs)


@dataclass(frozen=True)
class ModelSpec:
    context_window: int
    tokenizer_id: str
    chat_template: str | None = None
    close_generation_prompt_with_eos: bool = False


MODEL_SPECS: dict[str, ModelSpec] = {
    # DeepSeek 官方 OpenAI 兼容接口的 usage.prompt_tokens 也会把 assistant 空消息闭合（+eos）。
    "openai/deepseek-v4-flash": ModelSpec(
        context_window=1_000_000,
        tokenizer_id="deepseek-ai/DeepSeek-V3",
        close_generation_prompt_with_eos=True,
    ),
    "openai/deepseek-v4-pro": ModelSpec(
        context_window=1_000_000,
        tokenizer_id="deepseek-ai/DeepSeek-V3",
        close_generation_prompt_with_eos=True,
    ),
    # DashScope 的 OpenAI 兼容接口返回的 usage.prompt_tokens 口径会把 assistant 的空消息也“闭合”掉，
    # 等价于在 generation prompt 末尾额外追加一个 <|im_end|>（即 pkg.eos_token_id）。
    "openai/qwen3.5-flash": ModelSpec(
        context_window=256_000,
        tokenizer_id="Qwen/Qwen3.5-35B-A3B",
        close_generation_prompt_with_eos=True,
    ),
    "openai/qwen3.5-plus": ModelSpec(
        context_window=256_000,
        tokenizer_id="Qwen/Qwen3.5-35B-A3B",
        close_generation_prompt_with_eos=True,
    ),
}

DEFAULT_CONTEXT_WINDOW = 128_000

# 注意：部分 OpenAI 兼容 provider 会在 assistant message 上返回 `reasoning_content`。
# 这些字段在“是否会被下一轮模型调用当作上下文”上，各家口径并不一致：
# - 有的 provider 会把它当作上下文的一部分；
# - 有的 provider 可能不接受该字段，需要在发起下一轮请求前剔除（历史原因：deepseek-v3.2 曾有此约束）。
#
# 这里的 token 统计目标是“尽量贴近真实 prompt token”，用于 memory manager 的阈值判断：
# - 校验时允许该字段存在，避免 token 统计阶段直接失败；
# - 统计时不把它作为独立字段喂给 pkg（部分 pkg/chat_template 会对未知字段报错）；
# - 统一口径：把推理折叠进 `content` 再统计。

_ALLOWED_MESSAGE_KEYS = {
    "role",
    "content",
    "name",
    "tool_calls",
    "tool_call_id",
    "reasoning_content",
}


class TokenizerRegistry:
    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache: dict[str, Any] = {}
        self._cache_dir = Path(
            os.getenv("PROJECT_X_HF_CACHE_DIR", cache_dir or "~/.project-x/cache/huggingface")
        ).expanduser()

    def get_tokenizer(self, tokenizer_id: str) -> Any:
        if AutoTokenizer is None:
            raise TokenCounterError("未安装 transformers，无法进行精确 token 统计")
        cached = self._cache.get(tokenizer_id)
        if cached is not None:
            return cached
        # 先“只读本地缓存”，避免每次运行都去 HF Hub 做鉴权/版本检查（也会触发 unauthenticated warning）。
        # 如果本地没有，再回退到允许联网下载一次，后续就能命中缓存。
        try:
            tokenizer = _hf_from_pretrained(
                tokenizer_id,
                trust_remote_code=True,
                cache_dir=str(self._cache_dir),
                local_files_only=True,
            )
        except (OSError, ValueError, RuntimeError, TypeError):
            tokenizer = _hf_from_pretrained(
                tokenizer_id,
                trust_remote_code=True,
                cache_dir=str(self._cache_dir),
            )
        self._cache[tokenizer_id] = tokenizer
        return tokenizer


class TokenCounter:
    def __init__(self, registry: TokenizerRegistry | None = None) -> None:
        self._registry = registry or TokenizerRegistry()

    def context_window(self, model: str) -> int:
        """
        用于“按上下文窗口比例”做阈值判断时的上下文上限。

        - 已知模型：返回精确值
        - 未知模型：返回一个保守默认值（避免上层直接放弃逻辑）
        """
        spec = MODEL_SPECS.get(model)
        if spec is not None:
            return spec.context_window
        return DEFAULT_CONTEXT_WINDOW

    def count_text_tokens(self, model: str, text: str) -> tuple[int, bool]:
        # 临时策略：不调用任何 pkg，统一走估算口径。
        # 目的：避免本地/CI 里因为 transformers 依赖、HF 缓存、联网下载等不确定因素导致阻塞或失败。
        _ = model
        return ceil(len(text.encode("utf-8")) / 4), True

    def count_messages_tokens(self, model: str, messages: list[dict[str, Any]]) -> tuple[int, bool]:
        # 临时策略：不调用任何 pkg，统一走估算口径。
        # 注意：仍然保留 message 字段校验与 reasoning_content 折叠逻辑，避免上游把异常字段悄悄吞掉。
        self._validate_messages(messages)
        _ = model
        return self.estimate_messages_tokens_by_chars(model, messages), True

    def token_percentage(self, model: str, messages: list[dict[str, Any]]) -> tuple[int, bool]:
        tokens, is_estimate = self.count_messages_tokens(model, messages)
        context_window = self.context_window(model)
        return ceil(tokens * 100 / context_window), is_estimate

    def estimate_messages_tokens_by_chars(self, model: str, messages: list[dict[str, Any]]) -> int:
        self._validate_messages(messages)
        packed = "\n".join(
            self._pack_message_as_text(m)
            for m in self._sanitize_messages_for_chat_template(model=model, messages=messages)
        )
        return ceil(len(packed.encode("utf-8")) / 4)

    @staticmethod
    def _pack_message_as_text(message: dict[str, Any]) -> str:
        role = message.get("role", "")
        name = message.get("name")
        tool_call_id = message.get("tool_call_id")
        tool_calls = message.get("tool_calls")
        content = message.get("content", "")
        return (
            f"role={role}\n"
            f"name={name}\n"
            f"tool_call_id={tool_call_id}\n"
            f"tool_calls={tool_calls}\n"
            f"content={content}"
        )

    @staticmethod
    def _sanitize_messages_for_chat_template(*, model: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        统一 apply_chat_template 与估算口径：只保留 chat template 需要/支持的字段。

        - 统计时不能把 `reasoning_content` 作为独立字段传给 pkg（部分 pkg 会报错）
        - 但如果 provider 会把它计入上下文，则需要把它计入 token（折叠进 content）
        """
        sanitized: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "assistant":
                reasoning_text = msg.get("reasoning_content")
                if reasoning_text:
                    if content:
                        content = f"{reasoning_text}\n{content}"
                    else:
                        content = str(reasoning_text)

            packed: dict[str, Any] = {}
            for k in ("role", "name", "tool_calls", "tool_call_id"):
                if k in msg:
                    packed[k] = msg[k]
            if content is not None:
                packed["content"] = content
            sanitized.append(packed)
        return sanitized

    @staticmethod
    def _extract_input_ids(encoded: Any) -> list[int]:
        """
        transformers 的 apply_chat_template(tokenize=True) 在不同版本/不同 pkg 下可能返回：
        - list[int]
        - BatchEncoding（dict-like，含 input_ids）
        - dict（含 input_ids）

        这里统一抽取为 list[int]，避免误用 len(BatchEncoding)=字段数量 这种坑。
        """
        if isinstance(encoded, list):
            return encoded
        if isinstance(encoded, dict) and "input_ids" in encoded:
            return list(encoded["input_ids"])
        input_ids = getattr(encoded, "get", None)
        if callable(input_ids):
            maybe = encoded.get("input_ids")
            if maybe is not None:
                return list(maybe)
        raise TokenCounterError(f"apply_chat_template 返回值不包含 input_ids：type={type(encoded)}")

    @staticmethod
    def _spec(model: str) -> ModelSpec:
        spec = MODEL_SPECS.get(model)
        if spec is None:
            raise TokenCounterError(f"未知模型：{model}")
        return spec

    @staticmethod
    def _validate_messages(messages: list[dict[str, Any]]) -> None:
        for i, m in enumerate(messages):
            unknown = set(m.keys()) - _ALLOWED_MESSAGE_KEYS
            if unknown:
                raise TokenCounterError(f"messages[{i}] 包含未知字段：{sorted(unknown)}")
