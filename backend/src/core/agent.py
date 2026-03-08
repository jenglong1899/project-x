import queue
from typing import Any, Callable
from dataclasses import dataclass

from src.core.chat import stream, execute_tool_and_append, ResetContextDirective, ContinueLoopDirective
from src.core.model_config import ModelConfig
from src.core.policies import strip_reasoning_content_if_needed


@dataclass(frozen=True)
class QueuedUserMessage:
    id: str  # 前端渲染要用到
    content: str


class Agent:
    def __init__(self, *, name: str, model_config: ModelConfig,
                 system_instruction: str, user_instruction: str,
                 on_ai_content_delta: Callable[[str], None],
                 on_ai_reasoning_delta: Callable[[str], None]) -> None:
        self.name = name
        self._model_config = model_config
        self._messages: list[dict[str, Any]] = []
        self._system_instruction = system_instruction
        self._user_instruction = user_instruction
        self._on_ai_content_delta = on_ai_content_delta
        self._on_ai_reasoning_delta = on_ai_reasoning_delta

        self._user_msg_queue: queue.Queue[QueuedUserMessage] = queue.Queue()

    def new_session(self) -> None:
        self._messages = [
            {"role": "system", "content": self._system_instruction},
            {"role": "user", "content": self._user_instruction},
        ]

    def resume_session(self, *, session_id: str) -> None:
        pass

    def enqueue_user_message(self, *, msg_id: str, user_message: str,
                             on_user_msg_enqueued: Callable[[str, str], None]) -> None:

        self._user_msg_queue.put(QueuedUserMessage(msg_id, user_message))
        on_user_msg_enqueued(msg_id, user_message)

    def _safe_drain_user_message_queue(self, user_msg_queue: queue.Queue, messages: list[dict[str, Any]]) -> int:
        drained = 0
        while True:
            try:
                msg = user_msg_queue.get_nowait()
            except queue.Empty:
                return drained

            strip_reasoning_content_if_needed(model=self._model_config, messages=messages)
            drained += 1
            messages.append({"role": "user", "content": msg})

    @staticmethod
    def _safe_stream(*, model_config: ModelConfig,
                     messages: list[dict[str, Any]],
                     on_ai_content_delta: Callable[[str], None],
                     on_ai_reasoning_delta: Callable[[str], None]) -> dict[str, Any]:
        # 如果 Agent 之前正在运行，然后结果突然被中断了，
        # 那就可能导致 message 数组最后一个可能是 AI message with tool call，
        # 这种情况下就应该再续上之前的对话，不应该再调用 stream 以获得 AI message 了
        if messages[-1] is not None and messages[-1].get("role") == "assistant" and messages[-1].get("tool_call"):
            return messages[-1]

        # 最后一条消息是user message
        return stream(model_config=model_config, messages=messages,
                      on_ai_content_delta=on_ai_content_delta,
                      on_ai_reasoning_delta=on_ai_reasoning_delta)

    def _reset_context(self):
        pass

    def run(self) -> dict[str, Any]:
        self._safe_drain_user_message_queue(self._user_msg_queue, self._messages)
        while True:
            ai_msg_dict = self._safe_stream(model_config=self._model_config,
                                            messages=self._messages,
                                            on_ai_content_delta=self._on_ai_content_delta,
                                            on_ai_reasoning_delta=self._on_ai_reasoning_delta
                                            )
            if not ai_msg_dict.get("tool_calls"):
                return ai_msg_dict

            orchestrator_directive = execute_tool_and_append()
            if isinstance(orchestrator_directive, ResetContextDirective):
                self._reset_context()
                continue

            # steer message 注入点。在执行完toolcall后注入最符合直觉
            # 另外注意，我们是在_reset_context之后才注入，
            # 因为上下文越精简，ai表现越好，reset context的优先级应高于steer conversation
            self._safe_drain_user_message_queue(self._user_msg_queue, self._messages)
            continue
