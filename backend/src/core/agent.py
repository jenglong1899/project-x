from collections import deque
from typing import Any, Protocol
from dataclasses import dataclass

from src.commons import noop
from src.conversation_store import ConversationStore
from src.core.agent_base import AgentBase
from src.core.agent_turn import (
    stream,
    execute_tool_calls,
    OnAiContentDelta,
    OnAiReasoningDelta,
    OnAiToolCallStarted,
    OnAiToolCallArgumentsDelta,
    OnAiToolCallFinished,
    OnToolResult,
    Tool,
)
from src.core.memory_manager import MemoryForkedSubagentRunner, MemoryForkedSubagentRunnerBase
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


class OnSwitchConversation(Protocol):
    def __call__(self, *, visible_messages: list[dict[str, Any]]) -> None: ...


class OnPauseRequested(Protocol):
    def __call__(self) -> None: ...


class OnPaused(Protocol):
    def __call__(self) -> None: ...


class OnResumed(Protocol):
    def __call__(self) -> None: ...


class Agent(AgentBase):

    def __init__(self, *, name: str, model_config: ModelConfig,
                 system_instruction: str, user_instruction: str,
                 tools: list[Tool],
                 on_ai_content_delta: OnAiContentDelta | None = None,
                 on_ai_reasoning_delta: OnAiReasoningDelta | None = None,
                 on_ai_tool_call_started: OnAiToolCallStarted | None = None,
                 on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta | None = None,
                 on_ai_tool_call_finished: OnAiToolCallFinished | None = None,
                 on_tool_result: OnToolResult | None = None,
                 on_user_msg_enqueued: OnUserMsgEnqueued | None = None,
                 on_queued_user_msg_committed: OnQueuedUserMsgCommitted | None = None,
                 on_switch_conversation: OnSwitchConversation | None = None,
                 on_pause_requested: OnPauseRequested | None = None,
                 on_paused: OnPaused | None = None,
                 on_resumed: OnResumed | None = None,
                 memory_manager_runner: MemoryForkedSubagentRunnerBase | None = None,
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
        self._on_switch_conversation = on_switch_conversation or noop
        self._on_pause_requested = on_pause_requested or noop
        self._on_paused = on_paused or noop
        self._on_resumed = on_resumed or noop

        self._user_msg_queue: deque[QueuedUserMessage] = deque()
        self._memory_manager_runner = memory_manager_runner or MemoryForkedSubagentRunner()
        self._memory_manager_turn_interval = memory_manager_turn_interval
        self._worker_turns_since_memory_manager = 0
        self._memory_manager_awaken_count = 0
        self._loaded_main_memory_content = loaded_main_memory_content
        self._pause_requested = False
        self._paused = False

        # 调用Agent的必须先选择 conversation segment，
        # self._conversation_store 会在这两个函数中被初始化。
        self._conversation_store: ConversationStore | None = None

    def start_conversation(self) -> None:
        conversation_file_name = ConversationStore.find_latest_conversation_file_name()
        if conversation_file_name:
            self._load_conversation_from_file(conversation_file_name=conversation_file_name)
        else:
            self._start_new_conversation_segment()

    def _start_new_conversation_segment(self) -> None:
        self._messages = [
            {"role": "system", "content": self._system_instruction},
            {"role": "user", "content": self._user_instruction},
        ]
        self._pause_requested = False
        self._paused = False
        self._conversation_store = ConversationStore(
            system_instruction=self._system_instruction,
            user_instruction=self._user_instruction,
        )
        self._notify_switch_conversation(messages=self._messages)
        self._on_resumed()

    def _load_conversation_from_file(self, *, conversation_file_name: str) -> None:
        if self._user_msg_queue:
            raise RuntimeError("加载 conversation 文件之前不能有排队中的 user message")

        store = ConversationStore.load_from_conversation_file_name(conversation_file_name=conversation_file_name)
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
        self._pause_requested = store.pause_requested
        self._paused = store.paused

        self._messages = messages
        self._conversation_store = store
        self._notify_switch_conversation(messages=messages)
        self._notify_pause_state_for_new_connection()

    def _persist_pause_state(self) -> None:
        if self._conversation_store is None:
            raise ValueError("conversation store 还没有被初始化")
        self._conversation_store.update_pause_state(
            pause_requested=self._pause_requested,
            paused=self._paused,
        )

    @staticmethod
    def _visible_messages_from(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(message) for message in messages[2:]]

    def _notify_switch_conversation(self, *, messages: list[dict[str, Any]]) -> None:
        self._on_switch_conversation(visible_messages=self._visible_messages_from(messages))

    def _notify_pause_state_for_new_connection(self) -> None:
        # 新连接恢复 conversation 时，需要把 pause 状态补发给前端，
        # 否则 UI 会默认展示“未暂停”，与后端实际状态不一致。
        if self._paused:
            self._on_paused()
            return
        if self._pause_requested:
            self._on_pause_requested()

    def _require_conversation_store(self) -> ConversationStore:
        if self._conversation_store is None:
            raise RuntimeError("conversation_store 未初始化，请先调用 start_conversation()")
        return self._conversation_store

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        if self._paused or self._pause_requested:
            self.resume()
        self._user_msg_queue.append(QueuedUserMessage(frontend_msg_id, user_message))
        self._on_user_msg_enqueued(frontend_msg_id=frontend_msg_id)

    def request_pause(self) -> None:
        if self._paused:
            return
        self._pause_requested = True
        self._persist_pause_state()
        self._on_pause_requested()

    def resume(self) -> None:
        was_paused = self._paused
        was_pause_requested = self._pause_requested
        self._pause_requested = False
        self._paused = False
        self._persist_pause_state()
        if was_paused or was_pause_requested:
            self._on_resumed()

    def is_paused(self) -> bool:
        return self._paused

    def is_pause_requested(self) -> bool:
        return self._pause_requested

    def has_pending_work(self) -> bool:
        # 约束：如果 conversation 还没持久化（也就是还没真正“开始”），
        # 那么 run() 只有在有待处理的 user message 时才是可调用的（否则会抛错）。
        if self._user_msg_queue:
            return True

        if self._conversation_store is None or not self._conversation_store.has_persisted_conversation():
            return False

        if not self._messages:
            return False

        last = self._messages[-1]
        role = last.get("role")

        # 1) assistant(tool_calls) 说明工具还没真正执行完（可能是中断后续跑）。
        if role == "assistant" and last.get("tool_calls"):
            return True

        # 2) tool message 说明还欠一轮“工具结果后的 follow-up assistant”。
        if role == "tool":
            return True

        return False

    def _safe_drain_user_message_queue(self) -> None:
        conversation_store = self._require_conversation_store()
        while self._user_msg_queue:
            item = self._user_msg_queue.popleft()
            strip_reasoning_content_if_needed(model=self._model_config.model, messages=self._messages)
            user_message = {"role": "user", "content": item.content}
            self._messages.append(user_message)
            # 只有等到用户发送了一个消息 之后，才创建对话文件。
            # 不然用户创建了一个会话，但是没有说任何内容，然后这个对话文件就被持久化下来了，
            # 然后用户 resume conversation ，结果发现这玩意是空的，这就很不合理。
            if not conversation_store.has_persisted_conversation():
                conversation_store.start_with_first_user_message(user_content=item.content)
                self._persist_memory_manager_state()
            else:
                conversation_store.append_message(user_message)
            self._on_queued_user_msg_committed(frontend_msg_id=item.frontend_msg_id)

    def _persist_memory_manager_state(self) -> None:
        if self._conversation_store is None:
            return
        self._conversation_store.update_memory_manager_state(
            turns_since_memory_manager=self._worker_turns_since_memory_manager,
            awaken_count=self._memory_manager_awaken_count,
        )

    async def _maybe_wake_memory_manager(self) -> None:
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
            self._reset_context()

    def _append_runtime_message(self, message: dict[str, Any]) -> None:
        # 这个函数被用的地方都是在 run 函数的后方，
        # run开头就drain user message，这函数出来之后一定是已经有持久化文件了。
        self._messages.append(message)
        self._require_conversation_store().append_message(message)

    @staticmethod
    async def _safe_stream(*, model_config: ModelConfig,
                           messages: list[dict[str, Any]],
                           tools: list[Tool],
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

    def _reset_context(self) -> None:
        from src.core.prompts import (
            build_system_level_instruction_zh,
            build_user_level_instruction_zh,
            read_main_memory,
        )

        if self._conversation_store is None:
            raise RuntimeError("conversation_store 未初始化，无法 reset_context")

        self._system_instruction = build_system_level_instruction_zh()
        self._user_instruction = build_user_level_instruction_zh()
        self._loaded_main_memory_content = read_main_memory()
        self._worker_turns_since_memory_manager = 0
        self._memory_manager_awaken_count = 0
        self._pause_requested = False
        self._paused = False

        self._messages = [
            {"role": "system", "content": self._system_instruction},
            {"role": "user", "content": self._user_instruction},
        ]
        conversation_store = ConversationStore(
            system_instruction=self._system_instruction,
            user_instruction=self._user_instruction,
        )
        # 用 auto_reminder 作为新会话的第一条 user message，
        # 这样 conversation 文件会立刻创建且模型也能看到 reminder。
        conversation_store.start_with_first_user_message(
            user_content=RESET_CONTEXT_AUTO_REMINDER
        )
        self._conversation_store = conversation_store
        self._messages = conversation_store.build_messages_from_history()
        self._persist_pause_state()
        self._notify_switch_conversation(messages=self._messages)
        self._on_resumed()
        # 接下来 run() 会继续 while 循环，直接以 auto_reminder 为最后一条 user message 进行下一轮模型调用。

    async def run(self) -> dict[str, Any]:
        conversation_store = self._require_conversation_store()

        self._safe_drain_user_message_queue()
        if not conversation_store.has_persisted_conversation():
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
                if self._pause_requested:
                    # 为了让“暂停”在有pending user message的场景下也可靠生效：
                    # 即使本轮没有 tool_calls，只要本轮模型调用已经结束，
                    # 我们也要在回合边界暂停，阻止 controller 立刻进入下一轮模型调用。
                    self._pause_requested = False
                    self._paused = True
                    self._persist_pause_state()
                    self._on_paused()
                return ai_msg_dict

            tool_messages = await execute_tool_calls(
                ai_msg_dict=ai_msg_dict,
                tools_by_name=self._tools_by_name,
                on_tool_result=self._on_tool_result,
            )
            for tool_message in tool_messages:
                self._append_runtime_message(tool_message)

            await self._maybe_wake_memory_manager()

            if self._pause_requested:
                # 用户点击暂停，可能是想看一会，然后恢复运行之前，还要输入一些内容，
                # 所以暂停检查点应该在 drain user msg 之前。
                # 同时必须在 tool_messages 已经 append/persist 且 memory manager 唤醒结束之后，
                # 否则会造成“用户看到了工具结果，但 memory manager 状态没有同步”的错觉。
                self._pause_requested = False
                self._paused = True
                self._persist_pause_state()
                self._on_paused()
                return ai_msg_dict

            # steer message 注入点。在执行完toolcall后注入最符合直觉
            # 另外注意，我们是在 memory manager reset-context 之后才注入，
            # 因为上下文越精简，ai表现越好，reset context的优先级应高于steer conversation
            self._safe_drain_user_message_queue()
            continue
