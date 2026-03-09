import os
from dataclasses import dataclass


@dataclass
class ModelConfig:
    model: str
    base_url: str
    api_key: str


QWEN35PLUS = ModelConfig(model="openai/qwen3.5-plus",
                         base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                         api_key=os.getenv("DASHSCOPE_API_KEY"))

DEEPSEEK = ModelConfig(model="openai/deepseek-reasoner",
                       base_url="https://api.deepseek.com",
                       api_key=os.getenv("DEEPSEEK_API_KEY"))

MINIMAX_MAINLAND = ModelConfig(model="openai/MiniMax-M2.1",
                               base_url="https://api.minimaxi.com/v1",
                               api_key=os.getenv("MINIMAX_MAINLAND_API_KEY"))

MINIMAX_OVERSEA = ModelConfig(model="openai/MiniMax-M2.1",
                              base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
                              api_key=os.getenv("MINIMAX_OVERSEA_API_KEY"))

GEMINI = ModelConfig(model="openai/gemini-3-flash-preview",
                     base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                     api_key=os.getenv("GEMINI_API_KEY"))

GEMINI_OPENROUTER = ModelConfig(model="openrouter/google/gemini-3-flash-preview",
                                base_url="https://openrouter.ai/api/v1",
                                api_key=os.getenv("OPENROUTER_API_KEY"))
