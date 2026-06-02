import os
from dataclasses import dataclass


@dataclass
class ModelConfig:
    model: str
    base_url: str
    api_key: str
    # provider 用于决定走哪条推理后端：
    # - "litellm": 现有 OpenAI-compatible 路径（deepseek/qwen/mock）
    # - "openai-codex": 新增的 Codex OAuth 路径（后续实现）
    provider: str = "litellm"


def _getenv(key: str) -> str:
    """
    目的：避免在 import 时因为缺少环境变量直接 KeyError。

    - 本项目支持 `PROJECT_X_MODEL_CONFIG=mock`（不需要外部 API key）。
    - 但如果这里用 `os.environ[...]`，即使不选该 provider，也会在 import 阶段崩溃。
    """
    return os.getenv(key, "")


QWEN35FLASH = ModelConfig(
    model="openai/qwen3.5-flash",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key=_getenv("DASHSCOPE_API_KEY"),
)

QWEN35PLUS = ModelConfig(
    model="openai/qwen3.5-plus",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key=_getenv("DASHSCOPE_API_KEY"),
)

DEEPSEEKV4FLASH = ModelConfig(
    model="openai/deepseek-v4-flash",
    base_url="https://api.deepseek.com",
    api_key=_getenv("DEEPSEEK_API_KEY"),
)

DEEPSEEKV4PRO = ModelConfig(
    model="openai/deepseek-v4-pro",
    base_url="https://api.deepseek.com",
    api_key=_getenv("DEEPSEEK_API_KEY"),
)

# 本地 mock 模型：用于测试/E2E，不依赖外部 API。
MOCK = ModelConfig(model="mock", base_url="", api_key="")

OPENAI_CODEX = ModelConfig(
    model="gpt-5.4",
    # Codex 的 base_url 默认交给 CodexClient 走 env/默认值：
    # - PROJECT_X_CODEX_BASE_URL
    # - DEFAULT_CODEX_BASE_URL
    #
    # 如需覆盖（例如接入代理/自建网关），上游显式传 ModelConfig(base_url=...)。
    base_url="",
    api_key="",
    provider="openai-codex",
)
