from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from src.core.model_config import ModelConfig


def stream(*, model_config: ModelConfig,
           messages: list[dict[str, Any]],
           on_ai_content_delta: Callable[[str], None],
           on_ai_reasoning_delta: Callable[[str], None]) -> dict[str, Any]:
    raise NotImplementedError


@dataclass
class ContinueLoopDirective:
    pass


@dataclass
class ResetContextDirective:
    prompt_to_my_future_self: str


OrchestratorDirective: TypeAlias = ContinueLoopDirective | ResetContextDirective


def execute_tool_and_append() -> OrchestratorDirective:
    raise NotImplementedError
