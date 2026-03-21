import queue
from typing import Any, Protocol
from dataclasses import dataclass

from src.conversation_store import ConversationStore
from src.core.agent_turn import (
    stream,
    execute_tool_calls,
    OnAiContentDelta,
    OnAiReasoningDelta,
    OnAiToolCallStarted,
    OnAiToolCallArgumentsDelta,
    OnAiToolCallFinished,
    OnToolResult,
    ResetContextDirective,
    ToolSpec,
)
from src.core.model_config import ModelConfig
from src.core.policies import strip_reasoning_content_if_needed


@dataclass(frozen=True)
class QueuedUserMessage:
    frontend_msg_id: str  # 前端渲染要用到，这个id是前端生成并维护的
    content: str


class OnUserMsgEnqueued(Protocol):
    def __call__(self, *, frontend_msg_id: str) -> None: ...


class OnQueuedUserMsgCommitted(Protocol):
    def __call__(self, *, frontend_msg_id: str) -> None: ...


class Agent:

    # 见调用处，有了这个函数就不用每次都写 if callback is not None
    @staticmethod
    def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    def __init__(self, *, name: str, model_config: ModelConfig,
                 system_instruction: str, user_instruction: str,
                 tools: list[ToolSpec],
                 on_ai_content_delta: OnAiContentDelta | None = None,
                 on_ai_reasoning_delta: OnAiReasoningDelta | None = None,
                 on_ai_tool_call_started: OnAiToolCallStarted | None = None,
                 on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta | None = None,
                 on_ai_tool_call_finished: OnAiToolCallFinished | None = None,
                 on_tool_result: OnToolResult | None = None,
                 on_user_msg_enqueued: OnUserMsgEnqueued | None = None,
                 on_queued_user_msg_committed: OnQueuedUserMsgCommitted | None = None,
                 ) -> None:
        self.name = name
        self._model_config = model_config
        self._messages: list[dict[str, Any]] = []
        self._system_instruction = system_instruction
        self._user_instruction = user_instruction
        self._tools = tools
        self._tools_by_name = {tool.name: tool for tool in tools}
        if len(self._tools_by_name) != len(self._tools):
            raise ValueError("tools 里存在重复的 name")
        self._on_ai_content_delta = on_ai_content_delta or self._noop
        self._on_ai_reasoning_delta = on_ai_reasoning_delta or self._noop

        # started 不一定表示是函数的名字出来了，有些供应商是先给 ID 什么的
        self._on_ai_tool_call_started = on_ai_tool_call_started or self._noop
        self._on_ai_tool_call_arguments_delta = on_ai_tool_call_arguments_delta or self._noop
        self._on_ai_tool_call_finished = on_ai_tool_call_finished or self._noop
        self._on_tool_result = on_tool_result or self._noop

        self._on_user_msg_enqueued = on_user_msg_enqueued or self._noop
        self._on_queued_user_msg_committed = on_queued_user_msg_committed or self._noop

        self._user_msg_queue: queue.Queue[QueuedUserMessage] = queue.Queue()

        # 调用Agent的必须选择 new_conversation 或者 resume_conversation，
        # self._conversation_store 会在这两个函数中被初始化。
        self._conversation_store: ConversationStore | None = None

    def new_conversation(self) -> None:
        self._messages = [
            {"role": "system", "content": self._system_instruction},
            {"role": "user", "content": self._user_instruction},
        ]
        self._conversation_store = ConversationStore(
            system_instruction=self._system_instruction,
            user_instruction=self._user_instruction,
        )

    def resume_conversation(self, *, conversation_id: str) -> None:
        raise NotImplementedError

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        self._user_msg_queue.put(QueuedUserMessage(frontend_msg_id, user_message))
        self._on_user_msg_enqueued(frontend_msg_id=frontend_msg_id)

    def has_pending_user_messages(self) -> bool:
        return not self._user_msg_queue.empty()

    def _safe_drain_user_message_queue(self, user_msg_queue: queue.Queue[QueuedUserMessage],
                                       messages: list[dict[str, Any]]) -> int:
        drained = 0
        while True:
            try:
                item = user_msg_queue.get_nowait()
            except queue.Empty:
                return drained

            strip_reasoning_content_if_needed(model=self._model_config.model, messages=messages)
            drained += 1
            user_message = {"role": "user", "content": item.content}
            self._messages.append(user_message)
            # 只有等到用户发送了一个消息 之后，才创建对话文件。
            # 不然用户创建了一个会话，但是没有说任何内容，然后这个对话文件就被持久化下来了，
            # 然后用户 resume conversation ，结果发现这玩意是空的，这就很不合理。
            if not self._conversation_store.has_persisted_conversation():
                self._conversation_store.start_with_first_user_message(user_content=item.content)
            else:
                self._conversation_store.append_message(user_message)
            self._on_queued_user_msg_committed(frontend_msg_id=item.frontend_msg_id)

    def _append_runtime_message(self, message: dict[str, Any]) -> None:
        # 这个函数被用的地方都是在 run 函数的后方，
        # run开头就drain user message，这函数出来之后一定是已经有持久化文件了。
        self._messages.append(message)
        self._conversation_store.append_message(message)

    @staticmethod
    def _safe_stream(*, model_config: ModelConfig,
                     messages: list[dict[str, Any]],
                     tools: list[ToolSpec],
                     on_ai_content_delta: OnAiContentDelta,
                     on_ai_reasoning_delta: OnAiReasoningDelta,
                     on_ai_tool_call_started: OnAiToolCallStarted,
                     on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta,
                     on_ai_tool_call_finished: OnAiToolCallFinished) -> dict[str, Any]:
        """
        :return: ai message dict
        """
        # 如果 Agent 之前正在运行，然后结果突然被中断了，
        # 那就可能导致 message 数组最后一个可能是 AI message with tool call，
        # 这种情况下就应该再续上之前的对话，不应该再调用 stream 以获得 AI message 了
        if messages[-1] is not None and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls"):
            return messages[-1]

        # 最后一条消息是user message
        return stream(model_config=model_config, messages=messages,
                      tools=tools,
                      on_ai_content_delta=on_ai_content_delta,
                      on_ai_reasoning_delta=on_ai_reasoning_delta,
                      on_ai_tool_call_started=on_ai_tool_call_started,
                      on_ai_tool_call_arguments_delta=on_ai_tool_call_arguments_delta,
                      on_ai_tool_call_finished=on_ai_tool_call_finished)

    def _reset_context(self):
        raise NotImplementedError

    def run(self) -> dict[str, Any]:
        self._safe_drain_user_message_queue(self._user_msg_queue, self._messages)
        while True:
            ai_msg_dict = self._safe_stream(model_config=self._model_config,
                                            messages=self._messages,
                                            tools=self._tools,
                                            on_ai_content_delta=self._on_ai_content_delta,
                                            on_ai_reasoning_delta=self._on_ai_reasoning_delta,
                                            on_ai_tool_call_started=self._on_ai_tool_call_started,
                                            on_ai_tool_call_arguments_delta=self._on_ai_tool_call_arguments_delta,
                                            on_ai_tool_call_finished=self._on_ai_tool_call_finished,
                                            )
            if not self._messages or ai_msg_dict is not self._messages[-1]:
                self._append_runtime_message(ai_msg_dict)
            if not ai_msg_dict.get("tool_calls"):
                return ai_msg_dict

            tool_execution = execute_tool_calls(
                ai_msg_dict=ai_msg_dict,
                tools_by_name=self._tools_by_name,
                on_tool_result=self._on_tool_result,
            )
            for tool_message in tool_execution.tool_messages:
                self._append_runtime_message(tool_message)

            if isinstance(tool_execution.directive, ResetContextDirective):
                self._reset_context()
                continue

            # steer message 注入点。在执行完toolcall后注入最符合直觉
            # 另外注意，我们是在_reset_context之后才注入，
            # 因为上下文越精简，ai表现越好，reset context的优先级应高于steer conversation
            self._safe_drain_user_message_queue(self._user_msg_queue, self._messages)
            continue
