import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from src.core.agent import Agent, OnConversationPersisted, OnQueuedUserMsgCommitted, OnResetContext
from src.core.agent_turn import (
    OnAiContentDelta,
    OnAiReasoningDelta,
    OnAiToolCallArgumentsDelta,
    OnAiToolCallFinished,
    OnAiToolCallStarted,
    OnToolResult,
)
from src.core.model_config import (
    DEEPSEEK,
    GEMINI,
    GEMINI_OPENROUTER,
    MINIMAX_MAINLAND,
    MINIMAX_OVERSEA,
    MOCK,
    QWEN35PLUS,
    ModelConfig,
)
from src.prompts.builder import (
    build_system_level_instruction_zh,
    build_user_level_instruction_zh,
)
from src.tools.bash import BASH_TOOL
from src.tools.reset_context import RESET_CONTEXT_AUTO_REMINDER, RESET_CONTEXT_TOOL


logger = logging.getLogger(__name__)

MODEL_CONFIGS: dict[str, ModelConfig] = {
    "qwen35plus": QWEN35PLUS,
    "deepseek": DEEPSEEK,
    "gemini": GEMINI,
    "gemini_openrouter": GEMINI_OPENROUTER,
    "minimax_mainland": MINIMAX_MAINLAND,
    "minimax_oversea": MINIMAX_OVERSEA,
    "mock": MOCK,
}


class AgentLike(Protocol):
    def new_conversation(self) -> None: ...

    def resume_conversation(self, *, conversation_id: str) -> None: ...

    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None: ...

    def has_pending_user_messages(self) -> bool: ...

    async def run(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgentCallbacks:
    on_ai_content_delta: OnAiContentDelta
    on_ai_reasoning_delta: OnAiReasoningDelta
    on_ai_tool_call_started: OnAiToolCallStarted
    on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta
    on_ai_tool_call_finished: OnAiToolCallFinished
    on_tool_result: OnToolResult
    on_queued_user_msg_committed: OnQueuedUserMsgCommitted
    on_conversation_persisted: OnConversationPersisted
    on_reset_context: OnResetContext


class AgentFactory(Protocol):
    def __call__(self, *, callbacks: AgentCallbacks) -> AgentLike: ...


def resolve_model_config() -> ModelConfig:
    model_key = os.getenv("PROJECT_X_MODEL_CONFIG", "qwen35plus")
    model_config = MODEL_CONFIGS.get(model_key)
    if model_config is None:
        supported = ", ".join(sorted(MODEL_CONFIGS))
        raise RuntimeError(
            f"PROJECT_X_MODEL_CONFIG={model_key} 不受支持，可选值: {supported}"
        )
    if model_key == "mock":
        return model_config
    if not model_config.api_key:
        raise RuntimeError(f"{model_key} 对应的 API key 未配置")
    return model_config


def create_default_agent(*, callbacks: AgentCallbacks) -> Agent:
    return Agent(
        name="project-x-web",
        model_config=resolve_model_config(),
        system_instruction=build_system_level_instruction_zh(),
        user_instruction=build_user_level_instruction_zh(),
        tools=[BASH_TOOL, RESET_CONTEXT_TOOL],
        on_ai_content_delta=callbacks.on_ai_content_delta,
        on_ai_reasoning_delta=callbacks.on_ai_reasoning_delta,
        on_ai_tool_call_started=callbacks.on_ai_tool_call_started,
        on_ai_tool_call_arguments_delta=callbacks.on_ai_tool_call_arguments_delta,
        on_ai_tool_call_finished=callbacks.on_ai_tool_call_finished,
        on_tool_result=callbacks.on_tool_result,
        on_queued_user_msg_committed=callbacks.on_queued_user_msg_committed,
        on_conversation_persisted=callbacks.on_conversation_persisted,
        on_reset_context=callbacks.on_reset_context,
    )


@dataclass
class ToolProjectionState:
    tool_call_id: str
    tool_name: str
    index: int


class ChatEventProjector:
    def __init__(self, *, emit: "EventEmitter") -> None:
        self._emit = emit
        self._active_assistant_message_id: str | None = None
        self._tool_states: dict[str, ToolProjectionState] = {}

    def on_agent_became_busy(self) -> None:
        self._emit({"type": "generation.started"})

    def on_agent_became_idle(self) -> None:
        self._close_assistant_message()
        self._tool_states.clear()
        self._emit({"type": "generation.completed"})

    def on_agent_turn_completed(self) -> None:
        self._close_assistant_message()

    def on_user_message_committed(self, *, user_message_id: str, content: str) -> None:
        self._emit(
            {
                "type": "user.message.committed",
                "userMessageId": user_message_id,
                "content": content,
            }
        )

    def on_ai_content_delta(self, *, content_delta: str) -> None:
        self._emit_assistant_delta(channel="content", delta=content_delta)

    def on_ai_reasoning_delta(self, *, reasoning_delta: str) -> None:
        self._emit_assistant_delta(channel="reasoning", delta=reasoning_delta)

    def on_ai_tool_call_started(
        self,
        *,
        index: int,
        tool_call_id: str | None,
        tool_name: str | None,
    ) -> None:
        resolved_tool_call_id = self._require_tool_call_id(tool_call_id)
        self._close_assistant_message()
        self._ensure_tool_started(
            index=index,
            tool_call_id=resolved_tool_call_id,
            tool_name=tool_name,
        )

    def on_ai_tool_call_arguments_delta(
        self,
        *,
        index: int,
        tool_call_id: str | None,
        tool_name: str | None,
        arguments_delta: str,
    ) -> None:
        resolved_tool_call_id = self._require_tool_call_id(tool_call_id)
        self._close_assistant_message()
        tool_state = self._ensure_tool_started(
            index=index,
            tool_call_id=resolved_tool_call_id,
            tool_name=tool_name,
        )
        self._emit(
            {
                "type": "tool.arguments.delta",
                "toolCallId": resolved_tool_call_id,
                "toolName": tool_state.tool_name,
                "argumentsDelta": arguments_delta,
            }
        )

    def on_ai_tool_call_finished(
        self,
        *,
        index: int,
        tool_call_id: str | None,
        tool_name: str | None,
        arguments: str,
    ) -> None:
        resolved_tool_call_id = self._require_tool_call_id(tool_call_id)
        tool_state = self._ensure_tool_started(
            index=index,
            tool_call_id=resolved_tool_call_id,
            tool_name=tool_name,
        )
        self._emit(
            {
                "type": "tool.completed",
                "toolCallId": resolved_tool_call_id,
                "toolName": tool_state.tool_name,
                "arguments": arguments,
            }
        )

    def on_tool_result(
        self,
        *,
        tool_call_id: str | None,
        result_json_str: str,
    ) -> None:
        resolved_tool_call_id = self._require_tool_call_id(tool_call_id)
        self._emit(
            {
                "type": "tool.result",
                "toolCallId": resolved_tool_call_id,
                "result": result_json_str,
            }
        )

    def _emit_assistant_delta(self, *, channel: str, delta: str) -> None:
        message_id = self._ensure_assistant_message_started()
        self._emit(
            {
                "type": "assistant.message.delta",
                "messageId": message_id,
                "channel": channel,
                "delta": delta,
            }
        )

    def _ensure_assistant_message_started(self) -> str:
        if self._active_assistant_message_id is not None:
            return self._active_assistant_message_id

        message_id = uuid4().hex
        self._active_assistant_message_id = message_id
        self._emit(
            {
                "type": "assistant.message.started",
                "messageId": message_id,
            }
        )
        return message_id

    def _close_assistant_message(self) -> None:
        if self._active_assistant_message_id is None:
            return

        message_id = self._active_assistant_message_id
        self._active_assistant_message_id = None
        self._emit(
            {
                "type": "assistant.message.completed",
                "messageId": message_id,
            }
        )

    def _ensure_tool_started(
        self,
        *,
        index: int,
        tool_call_id: str,
        tool_name: str | None,
    ) -> ToolProjectionState:
        tool_state = self._tool_states.get(tool_call_id)
        if tool_state is not None:
            if tool_name and tool_state.tool_name == "未命名工具":
                tool_state.tool_name = tool_name
            return tool_state

        tool_state = ToolProjectionState(
            tool_call_id=tool_call_id,
            tool_name=tool_name or "未命名工具",
            index=index,
        )
        self._tool_states[tool_call_id] = tool_state
        self._emit(
            {
                "type": "tool.started",
                "toolCallId": tool_call_id,
                "toolName": tool_state.tool_name,
                "index": index,
            }
        )
        return tool_state

    @staticmethod
    def _require_tool_call_id(tool_call_id: str | None) -> str:
        if tool_call_id:
            return tool_call_id
        raise RuntimeError("模型返回的 tool_call_id 为空，当前前端协议不支持该情况")


class EventEmitter(Protocol):
    def __call__(self, event: dict[str, Any]) -> None: ...


class WebSocketChatSession:
    def __init__(
        self,
        *,
        agent_factory: AgentFactory | None = None,
        conversation_id: str | None = None,
    ) -> None:
        """
        WebSocket 连接的会话编排器：桥接 Agent（agent.py）和 WebSocket。
        :param agent_factory:
        :param conversation_id: 不填则 new_conversation，填写则 resume_conversation
        """
        self._outgoing_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._closed = False
        self._pending_user_contents: dict[str, str] = {}
        self._runner_task: asyncio.Task[None] | None = None
        self._projector = ChatEventProjector(emit=self._emit_sync)

        callbacks = AgentCallbacks(
            on_ai_content_delta=self._projector.on_ai_content_delta,
            on_ai_reasoning_delta=self._projector.on_ai_reasoning_delta,
            on_ai_tool_call_started=self._projector.on_ai_tool_call_started,
            on_ai_tool_call_arguments_delta=self._projector.on_ai_tool_call_arguments_delta,
            on_ai_tool_call_finished=self._projector.on_ai_tool_call_finished,
            on_tool_result=self._projector.on_tool_result,
            on_queued_user_msg_committed=self._on_queued_user_msg_committed,
            on_conversation_persisted=self._on_conversation_persisted,
            on_reset_context=self._on_reset_context,
        )
        self._agent = (agent_factory or create_default_agent)(callbacks=callbacks)
        if conversation_id:
            self._agent.resume_conversation(conversation_id=conversation_id)
        else:
            self._agent.new_conversation()

    async def next_event(self) -> dict[str, Any] | None:
        return await self._outgoing_queue.get()

    async def submit_user_message(self, *, user_message_id: str, content: str) -> None:
        if self._closed:
            raise RuntimeError("session 已关闭")

        self._pending_user_contents[user_message_id] = content

        self._agent.enqueue_user_message(
            frontend_msg_id=user_message_id,
            user_message=content,
        )

        if self._runner_task is None or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._run_agent_until_idle())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._outgoing_queue.put(None)

    def _emit_sync(self, event: dict[str, Any]) -> None:
        # 如果是用async的话，那就是await _emit，会导致让出控制权，
        # 而有些清空我们会塞大量的delta回去，这就导致前端收delta可能会延迟
        if self._closed:
            return
        self._outgoing_queue.put_nowait(event)

    def _on_conversation_persisted(self, *, conversation_id: str, display_name: str) -> None:
        self._emit_sync(
            {
                "type": "conversation.persisted",
                "conversationId": conversation_id,
                "displayName": display_name,
            }
        )

    def _on_reset_context(
        self,
        *,
        conversation_id: str,
        display_name: str,
    ) -> None:
        # 这里选择由后端直接推送 auto_reminder 的 user.message.committed，而不是让前端收到 reset.context 后再去 HTTP 拉取会话详情：
        # - 否则前端会出现“正在流式追加 items”与“loadConversation 覆盖 items”的竞态，容易丢流式内容；
        # - 后端按顺序推事件，前端按顺序渲染，最简单也最稳定。
        #
        # 备注：为什么不在 Agent._reset_context() 里直接“复用 on_queued_user_msg_committed”来发这条 user.message.committed？
        # - 因为 on_queued_user_msg_committed 的参数只有 frontend_msg_id，并不包含内容；
        # 因此这里由 WebSocket 投影层显式发送一条 user.message.committed，并保证它紧随 reset.context、先于任何 delta。
        self._emit_sync(
            {
                "type": "reset.context",
                "conversationId": conversation_id,
                "displayName": display_name,
            }
        )
        self._emit_sync(
            {
                "type": "user.message.committed",
                "userMessageId": f"auto-{uuid4().hex}",
                "content": RESET_CONTEXT_AUTO_REMINDER,
            }
        )

    async def _run_agent_until_idle(self) -> None:
        self._projector.on_agent_became_busy()
        try:
            while True:
                await self._agent.run()
                self._projector.on_agent_turn_completed()
                if self._closed:
                    self._runner_task = None
                    return
                if not self._agent.has_pending_user_messages():
                    self._runner_task = None
                    return
        except Exception as exc:
            logger.exception("WebSocketChatSession agent.run 失败")
            self._runner_task = None
            self._emit_sync(
                {
                    "type": "error",
                    "code": "agent_run_failed",
                    "message": str(exc),
                }
            )
        finally:
            self._projector.on_agent_became_idle()

    def _on_queued_user_msg_committed(self, *, frontend_msg_id: str) -> None:
        content = self._pending_user_contents.pop(frontend_msg_id, "")

        self._projector.on_user_message_committed(
            user_message_id=frontend_msg_id,
            content=content,
        )
