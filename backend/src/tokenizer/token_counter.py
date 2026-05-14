from __future__ import annotations

from dataclasses import dataclass
from math import ceil
import os
from pathlib import Path
from typing import Any, cast


try:
    from transformers import AutoTokenizer  # type: ignore
except Exception:  # pragma: no cover
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
        context_window=128_000,
        tokenizer_id="deepseek-ai/DeepSeek-V3",
        close_generation_prompt_with_eos=True,
    ),
    "openai/deepseek-v4-pro": ModelSpec(
        context_window=128_000,
        tokenizer_id="deepseek-ai/DeepSeek-V3",
        close_generation_prompt_with_eos=True,
    ),
    # DashScope 的 OpenAI 兼容接口返回的 usage.prompt_tokens 口径会把 assistant 的空消息也“闭合”掉，
    # 等价于在 generation prompt 末尾额外追加一个 <|im_end|>（即 tokenizer.eos_token_id）。
    "openai/qwen3.5-flash": ModelSpec(
        context_window=128_000,
        tokenizer_id="Qwen/Qwen2.5-72B-Instruct",
        close_generation_prompt_with_eos=True,
    ),
    "openai/qwen3.5-plus": ModelSpec(
        context_window=128_000,
        tokenizer_id="Qwen/Qwen2.5-72B-Instruct",
        close_generation_prompt_with_eos=True,
    ),
}

_ALLOWED_MESSAGE_KEYS = {"role", "content", "name", "tool_calls", "tool_call_id"}


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
        except Exception:
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

    def count_text_tokens(self, model: str, text: str) -> tuple[int, bool]:
        try:
            tokenizer = self._registry.get_tokenizer(self._spec(model).tokenizer_id)
            return len(tokenizer.encode(text)), False
        except Exception:
            return ceil(len(text.encode("utf-8")) / 4), True

    def count_messages_tokens(self, model: str, messages: list[dict[str, Any]]) -> tuple[int, bool]:
        self._validate_messages(messages)
        try:
            spec = self._spec(model)
            tokenizer = self._registry.get_tokenizer(spec.tokenizer_id)
            if not hasattr(tokenizer, "apply_chat_template"):
                raise TokenCounterError(f"{model} 对应 tokenizer 不支持 apply_chat_template，无法精确统计")
            template = spec.chat_template
            if template is not None:
                tokenizer.chat_template = template
            encoded = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
            )
            input_ids = self._extract_input_ids(encoded)
            if spec.close_generation_prompt_with_eos:
                eos_token_id = getattr(tokenizer, "eos_token_id", None)
                if eos_token_id is None:
                    raise TokenCounterError(f"{model} 的 tokenizer 未提供 eos_token_id，无法闭合 generation prompt")
                if not input_ids or input_ids[-1] != eos_token_id:
                    input_ids = [*input_ids, eos_token_id]
            return len(input_ids), False
        except Exception:
            return self.estimate_messages_tokens_by_chars(messages), True

    def token_percentage(self, model: str, messages: list[dict[str, Any]]) -> tuple[int, bool]:
        spec = self._spec(model)
        tokens, is_estimate = self.count_messages_tokens(model, messages)
        return ceil(tokens * 100 / spec.context_window), is_estimate

    def estimate_messages_tokens_by_chars(self, messages: list[dict[str, Any]]) -> int:
        self._validate_messages(messages)
        packed = "\n".join(self._pack_message_as_text(m) for m in messages)
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
    def _extract_input_ids(encoded: Any) -> list[int]:
        """
        transformers 的 apply_chat_template(tokenize=True) 在不同版本/不同 tokenizer 下可能返回：
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
