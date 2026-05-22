from typing import Any
from src.utils import is_deepseek_reasoner

# 下面是deepseek-v3.2的要求，deepseek-v4 没有这类要求。
# def strip_reasoning_content_if_needed(model: str, messages: list[dict[str, Any]]):
#     """
#     deepseek要求发送 user message 前必须去掉上一轮 assistant 的 reasoning_content。
#     """
#     if not is_deepseek_reasoner(model):
#         return
#     for msg in messages:
#         if msg.get("role") == "assistant" and "reasoning_content" in msg:
#             msg.pop("reasoning_content", None)
