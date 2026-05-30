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
    ModelConfig,
    DEEPSEEKV4FLASH,
    DEEPSEEKV4PRO,
    QWEN35FLASH,
    QWEN35PLUS,
    MOCK,
    OPENAI_CODEX,
)
from src.core.init_prompts import (
    build_system_level_instruction_zh,
    build_user_level_instruction_zh,
)
from src.tools.cwd_state import CwdState
from src.toolkits import build_worker_tools


logger = logging.getLogger(__name__)

MODEL_CONFIGS: dict[str, ModelConfig] = {
    "qwen3.5-flash": QWEN35FLASH,
    "qwen3.5-plus": QWEN35PLUS,
    "deepseek-v4-flash": DEEPSEEKV4FLASH,
    "deepseek-v4-pro": DEEPSEEKV4PRO,
    "mock": MOCK,
    "openai-codex": OPENAI_CODEX,
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
    # codex 才是最划算的，所以应该默认用 codex
    model_key = os.getenv("PROJECT_X_MODEL_CONFIG", "openai-codex")
    model_config = MODEL_CONFIGS.get(model_key)
    if model_config is None:
        supported = ", ".join(sorted(MODEL_CONFIGS))
        raise RuntimeError(
            f"PROJECT_X_MODEL_CONFIG={model_key} 不受支持，可选值: {supported}"
        )
    if model_key == "mock":
        return model_config
    if model_key == "openai-codex":
        # 尽量在“开始推理之前”就给出明确错误信息，避免走到 stream(provider=openai-codex)
        # 才因 resolve_codex_tokens(...) 抛错导致全链路不可用，且错误定位困难。
        try:
            from src.pkg.openai_codex.auth import resolve_codex_tokens

            resolve_codex_tokens(import_from_cli_if_missing=True)
        except Exception as exc:
            raise RuntimeError(
                "选择 openai-codex 需要本地 Codex OAuth 凭据，但当前环境未配置。\n"
                "- 方案 1：先用 Codex CLI 登录，确保 ~/.codex/auth.json 存在\n"
                "- 方案 2：切回无需 OAuth 的模型（例如设置 PROJECT_X_MODEL_CONFIG=mock 进行开发调试）"
            ) from exc
        return model_config
    if not model_config.api_key:
        raise RuntimeError(f"{model_key} 对应的 API key 未配置")
    return model_config


def create_default_agent(*, callbacks: AgentCallbacks) -> Agent:
    cwd_state = CwdState()
    model_config = resolve_model_config()
    return Agent(
        name="project-x-web",
        model_config=model_config,
        system_instruction=build_system_level_instruction_zh(),
        user_instruction=build_user_level_instruction_zh(),
        # bash 和 read_file 共享 cwd，所以这里必须给每个 Agent 创建独立状态，不能复用全局单例。
        tools=build_worker_tools(cwd_state=cwd_state, provider=model_config.provider),
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


class AgentRunnerFactory(Protocol):
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


def create_agent_runner(
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
        agent_runner_factory: AgentRunnerFactory | None = None,
    ) -> None:
        """
        WebSocket 连接的会话编排器：桥接 AgentRunner(agent_runner.py) 和 WebSocket。
        :param agent_runner_factory: 用于测试注入
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
        if agent_runner_factory is None:
            self._agent_runner = create_agent_runner(
                callbacks=callbacks,
                is_closed=lambda: self._closed,
                on_agent_became_busy=self._projector.on_agent_became_busy,
                on_agent_turn_completed=self._projector.on_agent_turn_completed,
                on_agent_became_idle=self._projector.on_agent_became_idle,
                on_error=self._on_agent_controller_error,
            )
        else:
            self._agent_runner = agent_runner_factory(
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

        logger.info(
            "WebSocketChatSession.submit_user_message：收到用户消息（msg_id=%s len=%s closed=%s）",
            user_message_id,
            len(content),
            self._closed,
        )
        self._pending_user_contents[user_message_id] = content

        self._agent_runner.submit_user_message(
            frontend_msg_id=user_message_id,
            user_message=content,
        )

    async def submit_pause_request(self) -> None:
        if self._closed:
            raise RuntimeError("session 已关闭")

        logger.info("WebSocketChatSession.submit_pause_request：收到暂停请求（closed=%s）", self._closed)
        self._agent_runner.request_pause()

    async def submit_resume(self) -> None:
        if self._closed:
            raise RuntimeError("session 已关闭")

        logger.info("WebSocketChatSession.submit_resume：收到恢复请求（closed=%s）", self._closed)
        self._agent_runner.resume()

    async def close(self) -> None:
        if self._closed:
            return
        logger.info("WebSocketChatSession.close：会话关闭")
        self._closed = True
        await self._outgoing_queue.put(None)

    def _emit_sync(self, event: dict[str, Any]) -> None:
        # 如果是用async的话，那就是await _emit，会导致让出控制权，
        # 而有些清空我们会塞大量的delta回去，这就导致前端收delta可能会延迟
        if self._closed:
            return
        event_type = event.get("type")
        if isinstance(event_type, str):
            if event_type != 'assistant.message.delta':
                logger.info("WebSocketChatSession.emit：%s", event_type)
            else:
                logger.debug("WebSocketChatSession.emit：%s", event_type)
        self._outgoing_queue.put_nowait(event)

    def _on_switch_conversation(self, *, visible_messages: list[dict[str, Any]]) -> None:
        logger.info("WebSocketChatSession._on_switch_conversation：切换会话（visible=%s）", len(visible_messages))
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
        logger.info(
            "WebSocketChatSession._on_queued_user_msg_committed：消息落盘（msg_id=%s len=%s pending=%s）",
            frontend_msg_id,
            len(content),
            len(self._pending_user_contents),
        )

        self._projector.on_user_message_committed(
            user_message_id=frontend_msg_id,
            content=content,
        )
