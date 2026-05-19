import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from src.core.agent import Agent, OnPaused, OnPauseRequested, OnQueuedUserMsgCommitted, OnResumed, OnSwitchConversation
from src.core.agent_runner import AgentRunner
from src.core.agent_turn import (
    OnAiContentDelta,
    OnAiReasoningDelta,
    OnAiToolCallArgumentsDelta,
    OnAiToolCallFinished,
    OnAiToolCallStarted,
    OnToolResult,
)
from src.core.model_config import (
    DEEPSEEKV4FLASH,
    MOCK,
    QWEN35PLUS,
    ModelConfig,
)
from src.core.init_prompts import (
    build_system_level_instruction_zh,
    build_user_level_instruction_zh,
)
from src.tools.bash import create_bash_tool
from src.tools.cwd_state import CwdState
from src.tools.insert_text import create_insert_text_tool
from src.tools.read_file import create_read_file_tool
from src.tools.replace_text import create_replace_text_tool


logger = logging.getLogger(__name__)

MODEL_CONFIGS: dict[str, ModelConfig] = {
    "qwen35plus": QWEN35PLUS,
    "deepseek": DEEPSEEKV4FLASH,
    "mock": MOCK,
}


@dataclass(frozen=True)
class AgentCallbacks:
    on_ai_content_delta: OnAiContentDelta
    on_ai_reasoning_delta: OnAiReasoningDelta
    on_ai_tool_call_started: OnAiToolCallStarted
    on_ai_tool_call_arguments_delta: OnAiToolCallArgumentsDelta
    on_ai_tool_call_finished: OnAiToolCallFinished
    on_tool_result: OnToolResult
    on_queued_user_msg_committed: OnQueuedUserMsgCommitted
    on_switch_conversation: OnSwitchConversation
    on_pause_requested: OnPauseRequested
    on_paused: OnPaused
    on_resumed: OnResumed


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
    cwd_state = CwdState()
    return Agent(
        name="project-x-web",
        model_config=resolve_model_config(),
        system_instruction=build_system_level_instruction_zh(),
        user_instruction=build_user_level_instruction_zh(),
        # bash 和 read_file 共享 cwd，所以这里必须给每个 Agent 创建独立状态，不能复用全局单例。
        tools=[
            create_bash_tool(cwd_state=cwd_state),
            create_read_file_tool(cwd_provider=cwd_state),
            create_replace_text_tool(cwd_provider=cwd_state),
            create_insert_text_tool(cwd_provider=cwd_state),
        ],
        on_ai_content_delta=callbacks.on_ai_content_delta,
        on_ai_reasoning_delta=callbacks.on_ai_reasoning_delta,
        on_ai_tool_call_started=callbacks.on_ai_tool_call_started,
        on_ai_tool_call_arguments_delta=callbacks.on_ai_tool_call_arguments_delta,
        on_ai_tool_call_finished=callbacks.on_ai_tool_call_finished,
        on_tool_result=callbacks.on_tool_result,
        on_queued_user_msg_committed=callbacks.on_queued_user_msg_committed,
        on_switch_conversation=callbacks.on_switch_conversation,
        on_pause_requested=callbacks.on_pause_requested,
        on_paused=callbacks.on_paused,
        on_resumed=callbacks.on_resumed,
    )


class AgentControllerFactory(Protocol):
    def __call__(
        self,
        *,
        callbacks: AgentCallbacks,
        is_closed: Callable[[], bool],
        on_agent_became_busy: Callable[[], None],
        on_agent_turn_completed: Callable[[], None],
        on_agent_became_idle: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> AgentRunner: ...


def create_agent_controller(
    *,
    callbacks: AgentCallbacks,
    is_closed: Callable[[], bool],
    on_agent_became_busy: Callable[[], None],
    on_agent_turn_completed: Callable[[], None],
    on_agent_became_idle: Callable[[], None],
    on_error: Callable[[Exception], None],
) -> AgentRunner:
    # 产品形态上不再暴露“会话列表/切换/显式恢复某个会话文件”给前端：
    # WebSocket 连接建立时永远走“自动恢复最近的 conversation segment”，
    # 如果本地还没有任何对话文件，则退化为 new_conversation。
    controller = AgentRunner(
        agent=create_default_agent(callbacks=callbacks),
        is_closed=is_closed,
        on_agent_became_busy=on_agent_became_busy,
        on_agent_turn_completed=on_agent_turn_completed,
        on_agent_became_idle=on_agent_became_idle,
        on_error=on_error,
    )
    controller.start()
    return controller


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
        self._emit({"type": "agent.became.busy"})

    def on_agent_became_idle(self) -> None:
        self._close_assistant_message()
        self._tool_states.clear()
        self._emit({"type": "agent.became.idle"})

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

    def on_pause_requested(self) -> None:
        self._emit({"type": "agent.pause.requested"})

    def on_paused(self) -> None:
        self._emit({"type": "agent.paused"})

    def on_resumed(self) -> None:
        self._emit({"type": "agent.resumed"})

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
        agent_controller_factory: AgentControllerFactory | None = None,
    ) -> None:
        """
        WebSocket 连接的会话编排器：桥接 AgentRunner(agent_runner.py) 和 WebSocket。
        :param agent_controller_factory: 用于测试注入
        """
        self._outgoing_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._closed = False
        self._pending_user_contents: dict[str, str] = {}
        self._projector = ChatEventProjector(emit=self._emit_sync)

        callbacks = AgentCallbacks(
            on_ai_content_delta=self._projector.on_ai_content_delta,
            on_ai_reasoning_delta=self._projector.on_ai_reasoning_delta,
            on_ai_tool_call_started=self._projector.on_ai_tool_call_started,
            on_ai_tool_call_arguments_delta=self._projector.on_ai_tool_call_arguments_delta,
            on_ai_tool_call_finished=self._projector.on_ai_tool_call_finished,
            on_tool_result=self._projector.on_tool_result,
            on_queued_user_msg_committed=self._on_queued_user_msg_committed,
            on_switch_conversation=self._on_switch_conversation,
            on_pause_requested=self._projector.on_pause_requested,
            on_paused=self._projector.on_paused,
            on_resumed=self._projector.on_resumed,
        )
        if agent_controller_factory is None:
            self._agent_controller = create_agent_controller(
                callbacks=callbacks,
                is_closed=lambda: self._closed,
                on_agent_became_busy=self._projector.on_agent_became_busy,
                on_agent_turn_completed=self._projector.on_agent_turn_completed,
                on_agent_became_idle=self._projector.on_agent_became_idle,
                on_error=self._on_agent_controller_error,
            )
        else:
            self._agent_controller = agent_controller_factory(
                callbacks=callbacks,
                is_closed=lambda: self._closed,
                on_agent_became_busy=self._projector.on_agent_became_busy,
                on_agent_turn_completed=self._projector.on_agent_turn_completed,
                on_agent_became_idle=self._projector.on_agent_became_idle,
                on_error=self._on_agent_controller_error,
            )

    async def next_event(self) -> dict[str, Any] | None:
        return await self._outgoing_queue.get()

    async def submit_user_message(self, *, user_message_id: str, content: str) -> None:
        if self._closed:
            raise RuntimeError("session 已关闭")

        self._pending_user_contents[user_message_id] = content

        self._agent_controller.submit_user_message(
            frontend_msg_id=user_message_id,
            user_message=content,
        )

    async def submit_pause_request(self) -> None:
        if self._closed:
            raise RuntimeError("session 已关闭")

        self._agent_controller.request_pause()

    async def submit_resume(self) -> None:
        if self._closed:
            raise RuntimeError("session 已关闭")

        self._agent_controller.resume()

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

    def _on_switch_conversation(self, *, visible_messages: list[dict[str, Any]]) -> None:
        self._emit_sync(
            {
                "type": "conversation.switched",
                "visibleMessages": visible_messages,
            }
        )

    def _on_agent_controller_error(self, exc: Exception) -> None:
        logger.exception("WebSocketChatSession agent.run 失败")
        self._emit_sync(
            {
                "type": "error",
                "code": "agent_run_failed",
                "message": str(exc),
            }
        )

    def _on_queued_user_msg_committed(self, *, frontend_msg_id: str) -> None:
        content = self._pending_user_contents.pop(frontend_msg_id, "")

        self._projector.on_user_message_committed(
            user_message_id=frontend_msg_id,
            content=content,
        )
