import os
from dataclasses import dataclass


@dataclass
class ModelConfig:
    model: str
    base_url: str
    api_key: str

QWEN35FLASH=ModelConfig(model="openai/qwen3.5-flash",
                       base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                       api_key=os.environ["DASHSCOPE_API_KEY"])

QWEN35PLUS = ModelConfig(model="openai/qwen3.5-plus",
                         base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                         api_key=os.environ["DASHSCOPE_API_KEY"])

DEEPSEEKV4FLASH = ModelConfig(model="openai/deepseek-v4-flash",
                              base_url="https://api.deepseek.com",
                              api_key=os.environ["DEEPSEEK_API_KEY"])

DEEPSEEKV4PRO = ModelConfig(model="openai/deepseek-v4-pro",
                            base_url="https://api.deepseek.com",
                            api_key=os.environ["DEEPSEEK_API_KEY"])


# 本地 mock 模型：用于测试/E2E，不依赖外部 API。
MOCK = ModelConfig(model="mock", base_url="", api_key="")
