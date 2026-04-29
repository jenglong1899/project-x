from collections import deque
from typing import Any, Protocol
from dataclasses import dataclass

from src.commons import noop
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
    ToolSpec,
)
from src.core.memory_manager import MemoryForkedSubagentRunner, MemoryForkedSubagentRunnerLike
from src.core.model_config import ModelConfig
from src.core.policies import strip_reasoning_content_if_needed
from src.tools.reset_context import RESET_CONTEXT_AUTO_REMINDER

MEMORY_MANAGER_TURN_INTERVAL = 20


@dataclass(frozen=True)
class QueuedUserMessage:
    frontend_msg_id: str  # 前端渲染要用到，这个id是前端生成并维护的
    content: str


class OnUserMsgEnqueued(Protocol):
    def __call__(self, *, frontend_msg_id: str) -> None: ...


class OnQueuedUserMsgCommitted(Protocol):
    def __call__(self, *, frontend_msg_id: str) -> None: ...


class OnConversationPersisted(Protocol):
    def __call__(self, *, conversation_id: str, display_name: str) -> None: ...


class OnResetContext(Protocol):
    def __call__(self, *, conversation_id: str, display_name: str) -> None: ...


class Agent:

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
                 on_conversation_persisted: OnConversationPersisted | None = None,
                 on_reset_context: OnResetContext | None = None,
                 memory_manager_runner: MemoryForkedSubagentRunnerLike | None = None,
                 memory_manager_turn_interval: int = MEMORY_MANAGER_TURN_INTERVAL,
                 loaded_main_memory_content: str = "",
                 ) -> None:
        if memory_manager_turn_interval <= 0:
            raise ValueError("memory_manager_turn_interval 必须大于 0")

        self.name = name
        self._model_config = model_config
        self._messages: list[dict[str, Any]] = []
        self._system_instruction = system_instruction
        self._user_instruction = user_instruction
        self._tools = tools
        self._tools_by_name = {tool.name: tool for tool in tools}
        if len(self._tools_by_name) != len(self._tools):
            raise ValueError("tools 里存在重复的 name")
        self._on_ai_content_delta = on_ai_content_delta or noop
        self._on_ai_reasoning_delta = on_ai_reasoning_delta or noop

        # started 不一定表示是函数的名字出来了，有些供应商是先给 ID 什么的
        self._on_ai_tool_call_started = on_ai_tool_call_started or noop
        self._on_ai_tool_call_arguments_delta = on_ai_tool_call_arguments_delta or noop
        self._on_ai_tool_call_finished = on_ai_tool_call_finished or noop
        self._on_tool_result = on_tool_result or noop

        self._on_user_msg_enqueued = on_user_msg_enqueued or noop
        self._on_queued_user_msg_committed = on_queued_user_msg_committed or noop
        self._on_conversation_persisted = on_conversation_persisted or noop
        self._on_reset_context = on_reset_context or noop

        self._user_msg_queue: deque[QueuedUserMessage] = deque()
        self._memory_manager_runner = memory_manager_runner or MemoryForkedSubagentRunner()
        self._memory_manager_turn_interval = memory_manager_turn_interval
        self._worker_turns_since_memory_manager = 0
        self._memory_manager_awaken_count = 0
        self._loaded_main_memory_content = loaded_main_memory_content

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
        if self._user_msg_queue:
            raise RuntimeError("resume_conversation 之前不能有排队中的 user message")

        store = ConversationStore.load_from_conversation_id(conversation_id=conversation_id)
        messages = store.build_messages_from_history()
        if len(messages) < 2:
            raise ValueError("缺少 system/user level instruction，无法恢复")

        system_msg = messages[0]
        user_instruction_msg = messages[1]
        if system_msg.get("role") != "system" or not isinstance(system_msg.get("content"), str):
            raise ValueError("conversation 第一条消息必须是 system instruction")
        if user_instruction_msg.get("role") != "user" or not isinstance(user_instruction_msg.get("content"), str):
            raise ValueError("conversation 第二条消息必须是 user instruction")

        # 继续旧对话时，system/user instruction 以历史为准。
        self._system_instruction = system_msg["content"]
        self._user_instruction = user_instruction_msg["content"]
        self._worker_turns_since_memory_manager = store.memory_manager_turns_since_memory_manager
        self._memory_manager_awaken_count = store.memory_manager_awaken_count

        self._messages = messages
        self._conversation_store = store

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        self._user_msg_queue.append(QueuedUserMessage(frontend_msg_id, user_message))
        self._on_user_msg_enqueued(frontend_msg_id=frontend_msg_id)

    def has_pending_user_messages(self) -> bool:
        return bool(self._user_msg_queue)

    def _safe_drain_user_message_queue(self) -> int:
        drained = 0
        while self._user_msg_queue:
            item = self._user_msg_queue.popleft()
            strip_reasoning_content_if_needed(model=self._model_config.model, messages=self._messages)
            drained += 1
            user_message = {"role": "user", "content": item.content}
            self._messages.append(user_message)
            # 只有等到用户发送了一个消息 之后，才创建对话文件。
            # 不然用户创建了一个会话，但是没有说任何内容，然后这个对话文件就被持久化下来了，
            # 然后用户 resume conversation ，结果发现这玩意是空的，这就很不合理。
            if not self._conversation_store.has_persisted_conversation():
                self._conversation_store.start_with_first_user_message(user_content=item.content)
                # 首次持久化后，把 conversationId 通知到外层（例如 WebSocket 层），用于前端展示/列表更新。
                self._on_conversation_persisted(
                    conversation_id=self._conversation_store.conversation_id,
                    display_name=self._conversation_store.display_name,
                )
                self._persist_memory_manager_state()
            else:
                self._conversation_store.append_message(user_message)
            self._on_queued_user_msg_committed(frontend_msg_id=item.frontend_msg_id)
        return drained

    def _persist_memory_manager_state(self) -> None:
        if self._conversation_store is None:
            return
        self._conversation_store.update_memory_manager_state(
            turns_since_memory_manager=self._worker_turns_since_memory_manager,
            awaken_count=self._memory_manager_awaken_count,
        )

    async def _maybe_reset_context(self) -> None:
        self._worker_turns_since_memory_manager += 1
        if self._worker_turns_since_memory_manager < self._memory_manager_turn_interval:
            self._persist_memory_manager_state()
            return

        self._worker_turns_since_memory_manager = 0
        self._persist_memory_manager_state()
        result = await self._memory_manager_runner.run(
            worker_messages=self._messages,
            model_config=self._model_config,
            tools=self._tools,
            is_first_time_awaken=self._memory_manager_awaken_count == 0,
            loaded_main_memory_content=self._loaded_main_memory_content,
        )
        self._memory_manager_awaken_count += 1
        self._persist_memory_manager_state()

        if result.requested_reset_context:
            self._start_new_context_with_auto_reminder()

    def _append_runtime_message(self, message: dict[str, Any]) -> None:
        # 这个函数被用的地方都是在 run 函数的后方，
        # run开头就drain user message，这函数出来之后一定是已经有持久化文件了。
        self._messages.append(message)
        self._conversation_store.append_message(message)

    @staticmethod
    async def _safe_stream(*, model_config: ModelConfig,
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
        return await stream(model_config=model_config, messages=messages,
                            tools=tools,
                            on_ai_content_delta=on_ai_content_delta,
                            on_ai_reasoning_delta=on_ai_reasoning_delta,
                            on_ai_tool_call_started=on_ai_tool_call_started,
                            on_ai_tool_call_arguments_delta=on_ai_tool_call_arguments_delta,
                            on_ai_tool_call_finished=on_ai_tool_call_finished)

    def _start_new_context_with_auto_reminder(self) -> None:
        from src.prompts.builder import (
            build_system_level_instruction_zh,
            build_user_level_instruction_zh,
            read_main_memory,
        )

        if self._conversation_store is None:
            raise RuntimeError("conversation_store 未初始化，无法 reset_context")
        display_name = self._conversation_store.display_name or "新对话"

        self._system_instruction = build_system_level_instruction_zh()
        self._user_instruction = build_user_level_instruction_zh()
        self._loaded_main_memory_content = read_main_memory()
        self._worker_turns_since_memory_manager = 0
        self._memory_manager_awaken_count = 0
        self.new_conversation()

        auto_reminder = RESET_CONTEXT_AUTO_REMINDER

        # 用 auto_reminder 作为新会话的第一条 user message，
        # 这样 conversation 文件会立刻创建且模型也能看到 reminder。
        self._conversation_store.start_with_first_user_message(
            user_content=auto_reminder,
            display_name=display_name,
        )
        self._messages = self._conversation_store.build_messages_from_history()
        self._on_reset_context(
            conversation_id=self._conversation_store.conversation_id,
            display_name=self._conversation_store.display_name,
        )
        # 接下来 run() 会继续 while 循环，直接以 auto_reminder 为最后一条 user message 进行下一轮模型调用。

    async def run(self) -> dict[str, Any]:
        if self._conversation_store is None:
            raise RuntimeError("conversation_store 未初始化，请先调用 new_conversation() 或 resume_conversation()")

        self._safe_drain_user_message_queue()
        if not self._conversation_store.has_persisted_conversation():
            # 显式校验：如果没有待处理的 user message，就不应该进入模型生成路径。
            # 否则会进入 _append_runtime_message -> ConversationStore.append_message，最终抛出更隐晦的异常。
            raise RuntimeError("conversation 尚未开始：没有待处理的 user message，请先 enqueue_user_message()")

        while True:
            ai_msg_dict = await self._safe_stream(model_config=self._model_config,
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

            tool_execution = await execute_tool_calls(
                ai_msg_dict=ai_msg_dict,
                tools_by_name=self._tools_by_name,
                on_tool_result=self._on_tool_result,
            )
            for tool_message in tool_execution.tool_messages:
                self._append_runtime_message(tool_message)

            await self._maybe_reset_context()

            # steer message 注入点。在执行完toolcall后注入最符合直觉
            # 另外注意，我们是在 memory manager reset-context 之后才注入，
            # 因为上下文越精简，ai表现越好，reset context的优先级应高于steer conversation
            self._safe_drain_user_message_queue()
            continue
