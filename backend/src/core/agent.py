import asyncio
import logging
from collections import deque
from typing import Any, Protocol
from dataclasses import dataclass

from src.commons import noop
from src.conversation_store import ConversationStore
from src.core.agent_base import AgentBase, DriveDecision, DriveReason
from src.core.agent_turn import (
    stream,
    execute_tool_calls,
    OnAiContentDelta,
    OnAiReasoningDelta,
    OnAiToolCallStarted,
    OnAiToolCallArgumentsDelta,
    OnAiToolCallFinished,
    OnToolResult,
)
from src.tools.tool import Tool
from src.commons import WAKE_MM_SUMMARY_FLAG
from src.core.memory_manager import (
    MemoryManagerJudgeResetContextRunner,
    MemoryManagerSummaryRunner,
)
from src.core.model_config import ModelConfig
from src.pkg.token_counter import TokenCounter
from src.toolkits import build_memory_manager_summary_tools

MEMORY_MANAGER_CONTEXT_USED_THRESHOLD_STEP_PERCENT = 3

logger = logging.getLogger(__name__)


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
                 token_counter: TokenCounter | None = None,
                 ) -> None:
        self.name = name
        self._model_config = model_config
        self._messages: list[dict[str, Any]] = []
        self._system_instruction = system_instruction
        self._user_instruction = user_instruction
        self._tools = tools
        if len({tool.name for tool in tools}) != len(tools):
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
        self._memory_manager_summary_runner = MemoryManagerSummaryRunner()
        self._memory_manager_judge_runner = MemoryManagerJudgeResetContextRunner()
        self._memory_manager_summary_task: asyncio.Task[None] | None = None
        self._memory_manager_judge_task: asyncio.Task[bool] | None = None
        self._memory_manager_summary_awaken_count = 0
        self._memory_manager_judge_awaken_count = 0
        self._token_counter = token_counter or TokenCounter()
        self._pause_requested = False
        self._paused = False

        # self._conversation_store 会在start_conversation()中被初始化。
        self._conversation_store: ConversationStore | None = None

    def start_conversation(self) -> None:
        conversation_file_name = ConversationStore.find_latest_conversation_file_name()
        if conversation_file_name:
            logger.info("Agent[%s].start_conversation：恢复历史会话（file=%s）", self.name, conversation_file_name)
            self._load_conversation_from_file(conversation_file_name=conversation_file_name)
        else:
            logger.info("Agent[%s].start_conversation：创建新会话", self.name)
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
        self._memory_manager_summary_awaken_count = store.memory_manager_summary_awaken_count
        self._memory_manager_judge_awaken_count = store.memory_manager_judge_awaken_count
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
            logger.info(
                "Agent[%s].enqueue_user_message：暂停中自动恢复（paused=%s pause_requested=%s）",
                self.name,
                self._paused,
                self._pause_requested,
            )
            self.resume()
        self._user_msg_queue.append(QueuedUserMessage(frontend_msg_id, user_message))
        logger.info(
            "Agent[%s].enqueue_user_message：已入队（frontend_msg_id=%s queue=%s user_len=%s）",
            self.name,
            frontend_msg_id,
            len(self._user_msg_queue),
            len(user_message),
        )
        self._on_user_msg_enqueued(frontend_msg_id=frontend_msg_id)

    def request_pause(self) -> None:
        if self._paused:
            return
        self._pause_requested = True
        self._persist_pause_state()
        logger.info("Agent[%s].request_pause：pause_requested=true", self.name)
        self._on_pause_requested()

    def resume(self) -> None:
        was_paused = self._paused
        was_pause_requested = self._pause_requested
        self._pause_requested = False
        self._paused = False
        self._persist_pause_state()
        if was_paused or was_pause_requested:
            logger.info("Agent[%s].resume：已恢复（was_paused=%s was_pause_requested=%s）", self.name, was_paused, was_pause_requested)
            self._on_resumed()

    def is_paused(self) -> bool:
        return self._paused

    def is_pause_requested(self) -> bool:
        return self._pause_requested

    def drive_decision(self) -> DriveDecision:
        backlog_reason = self._backlog_reason()

        # paused 是一个硬边界：一旦进入 paused，runner 必须停下，等待显式 resume。
        if self._paused:
            if backlog_reason is None:
                return DriveDecision(should_drive=False, reason=DriveReason.paused_no_backlog)
            return DriveDecision(should_drive=False, reason=DriveReason.paused_with_backlog)

        if backlog_reason is not None:
            return DriveDecision(should_drive=True, reason=backlog_reason)

        # 无 backlog，但也要区分“未开始”与“正常 idle”，方便上层做更可读的判断/埋点。
        if self._conversation_store is None or not self._conversation_store.has_persisted_conversation():
            return DriveDecision(should_drive=False, reason=DriveReason.not_started)

        return DriveDecision(should_drive=False, reason=DriveReason.no_backlog)

    def _backlog_reason(self) -> DriveReason | None:
        if self._user_msg_queue:
            return DriveReason.backlog_user_msg

        if self._conversation_store is None or not self._conversation_store.has_persisted_conversation():
            return None

        if not self._messages:
            return None

        last = self._messages[-1]
        role = last.get("role")

        # 1) assistant(tool_calls) 说明工具还没真正执行完（可能是中断后续跑）。
        if role == "assistant" and last.get("tool_calls"):
            return DriveReason.backlog_tool_execution

        # 2) tool message 说明还欠一轮“工具结果后的 follow-up assistant”。
        # （可能是被中断了导致的）
        if role == "tool":
            return DriveReason.backlog_tool_followup

        return None

    def _safe_drain_user_message_queue(self) -> None:
        conversation_store = self._require_conversation_store()
        while self._user_msg_queue:
            item = self._user_msg_queue.popleft()
            user_message = {"role": "user", "content": item.content}
            self._messages.append(user_message)
            logger.info(
                "Agent[%s]._safe_drain_user_message_queue：出队并持久化（frontend_msg_id=%s remaining=%s user_len=%s）",
                self.name,
                item.frontend_msg_id,
                len(self._user_msg_queue),
                len(item.content),
            )
            # 只有等到用户发送了一个消息 之后，才创建对话文件。
            # 不然用户创建了一个会话，但是没有说任何内容，然后这个对话文件就被持久化下来了，
            # 然后用户 resume conversation ，结果发现这玩意是空的，这就很不合理。
            if not conversation_store.has_persisted_conversation():
                conversation_store.start_with_first_user_message(user_content=item.content)
                logger.info(
                    "Agent[%s] 会话已持久化（file=%s）",
                    self.name,
                    conversation_store.conversation_file_name,
                )
                self._persist_memory_manager_state()
            else:
                conversation_store.append_message(user_message)
            self._on_queued_user_msg_committed(frontend_msg_id=item.frontend_msg_id)

    def _persist_memory_manager_state(self) -> None:
        if self._conversation_store is None:
            return
        self._conversation_store.update_memory_manager_state(
            summary_awaken_count=self._memory_manager_summary_awaken_count,
            judge_awaken_count=self._memory_manager_judge_awaken_count,
        )

    @staticmethod
    def _observe_background_task_exceptions(*, task: asyncio.Task[Any], task_name: str, agent_name: str) -> None:
        """
        目的：确保后台 task 的异常一定会被观察到，否则 asyncio 会报：
        "Task exception was never retrieved"

        这里用 done callback 主动调用 task.exception() 完成“异常领取”，并打日志。
        """

        def _on_done(done_task: asyncio.Task[Any]) -> None:
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                logger.info("Agent[%s] 后台任务 %s 被取消", agent_name, task_name)
                return
            except Exception as exc:
                # 这里是 done callback，任何异常都不应继续向外冒泡，否则会污染事件循环日志。
                logger.exception(
                    "Agent[%s] 读取后台任务 %s 的异常时失败：%s: %s",
                    agent_name,
                    task_name,
                    type(exc).__name__,
                    exc,
                )
                return

            if exc is not None:
                logger.error("Agent[%s] 后台任务 %s 异常退出", agent_name, task_name, exc_info=exc)

        task.add_done_callback(_on_done)


    async def _maybe_wake_memory_manager(self) -> None:
        conversation_store = self._require_conversation_store()
        context_limit = self._token_counter.context_window(self._model_config.model)
        current_tokens, _is_estimate = self._token_counter.count_messages_tokens(self._model_config.model, self._messages)
        last_triggered_threshold = conversation_store.memory_manager_last_triggered_threshold

        used_percent = int(current_tokens * 100 / context_limit)
        current_threshold = (used_percent // MEMORY_MANAGER_CONTEXT_USED_THRESHOLD_STEP_PERCENT) * MEMORY_MANAGER_CONTEXT_USED_THRESHOLD_STEP_PERCENT
        if current_threshold <= last_triggered_threshold:
            return

        logger.info(
            "Agent[%s] 唤醒 memory manager（file=%s tokens=%s used_percent=%s last_threshold=%s current_threshold=%s step=%s）",
            self.name,
            conversation_store.conversation_file_name,
            current_tokens,
            used_percent,
            last_triggered_threshold,
            current_threshold,
            MEMORY_MANAGER_CONTEXT_USED_THRESHOLD_STEP_PERCENT,
        )
        conversation_store.update_memory_manager_last_triggered_threshold(last_triggered_threshold=current_threshold)

        summary_task = self._memory_manager_summary_task
        if summary_task is None or summary_task.done():
            summary_round = self._memory_manager_summary_awaken_count + 1
            worker_messages_snapshot = [dict(message) for message in self._messages]
            summary_tools = build_memory_manager_summary_tools(provider=self._model_config.provider)
            logger.info(
                "Agent[%s] 启动 memory_manager_summary_task（round=%s messages=%s）",
                self.name,
                summary_round,
                len(worker_messages_snapshot),
            )
            summary_task = asyncio.create_task(
                self._memory_manager_summary_runner.run(
                    worker_messages=worker_messages_snapshot,
                    model_config=self._model_config,
                    tools=summary_tools,
                    is_first_time_awaken=self._memory_manager_summary_awaken_count == 0,
                    conversation_file_name=conversation_store.conversation_file_name,
                    awaken_round=summary_round,
                )
            )
            self._observe_background_task_exceptions(
                task=summary_task,
                task_name="memory_manager_summary_task",
                agent_name=self.name,
            )
            self._memory_manager_summary_task = summary_task
            self._append_runtime_message({"role": "user", "content": WAKE_MM_SUMMARY_FLAG})
            self._memory_manager_summary_awaken_count = summary_round
            self._persist_memory_manager_state()

        judge_task = self._memory_manager_judge_task
        if judge_task is None or judge_task.done():
            judge_round = self._memory_manager_judge_awaken_count + 1
            worker_messages_snapshot = [dict(message) for message in self._messages]
            judge_tools = build_memory_manager_summary_tools(provider=self._model_config.provider)
            logger.info(
                "Agent[%s] 启动 memory_manager_judge_task（round=%s messages=%s）",
                self.name,
                judge_round,
                len(worker_messages_snapshot),
            )
            self._memory_manager_judge_task = asyncio.create_task(
                self._memory_manager_judge_runner.run(
                    worker_messages=worker_messages_snapshot,
                    model_config=self._model_config,
                    tools=judge_tools,
                    conversation_file_name=conversation_store.conversation_file_name,
                    awaken_round=judge_round,
                )
            )
            judge_task = self._memory_manager_judge_task
            self._memory_manager_judge_awaken_count = judge_round
            self._persist_memory_manager_state()
        if judge_task is None:
            logger.warning("memory manager judge task 未初始化，本次跳过 judge")
            return
        should_reset_context = await judge_task
        logger.info("Agent[%s] memory manager judge 结果（should_reset_context=%s）", self.name, should_reset_context)
        if should_reset_context:
            # 这里不需要额外 request_pause()：
            # 当前 run() 正在 await _maybe_wake_memory_manager()，后面的 drain queue / 下一轮 stream 都不会继续执行；
            # 同时 AgentRunner 会防止并发 run()，summary/judge 也只操作快照，不会回写 self._messages。
            # 所以从这里开始到 reset 完成，worker messages 已经处于“隐式冻结”状态。
            summary_task = self._memory_manager_summary_task
            if summary_task is not None and not summary_task.done():
                try:
                    await summary_task
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # reset_context 的用户体验优先于 summary 完整性；summary 失败只打日志，不阻断主流程。
                    logger.exception(
                        "Agent[%s] 等待 memory_manager_summary_task 时失败，仍继续 reset_context：%s: %s",
                        self.name,
                        type(exc).__name__,
                        exc,
                    )
            await self._run_memory_manager_summary_before_reset()
            logger.info("Agent[%s] 开始 reset_context", self.name)
            self._reset_context()

    async def _run_memory_manager_summary_before_reset(self) -> None:
        if not any(message.get("content") == WAKE_MM_SUMMARY_FLAG for message in self._messages):
            logger.warning("Agent[%s] reset 前未找到 %s，跳过收尾 summary", self.name, WAKE_MM_SUMMARY_FLAG)
            return

        summary_round = self._memory_manager_summary_awaken_count + 1
        logger.info(
            "Agent[%s] reset 前执行收尾 memory_manager_summary_task（round=%s messages=%s）",
            self.name,
            summary_round,
            len(self._messages),
        )
        # 注意FLAG是为下一次summary唤醒而留的，当次summary用的是上一个summary唤起时留下的flag
        # 如果这里再插一个新 flag，会变成：
        # ......旧内容...... [旧 flag] ......新内容...... [新 flag]
        #                                               ^ 最近一条 flag
        # 那 summary 就会认为“新 flag 之前都已经摘要过了”，结果真正该补的 旧 flag -> 新 flag 这一段反而被跳过。
        try:
            await self._memory_manager_summary_runner.run(
                worker_messages=[dict(message) for message in self._messages],
                model_config=self._model_config,
                tools=build_memory_manager_summary_tools(provider=self._model_config.provider),
                is_first_time_awaken=False,
                conversation_file_name=self._require_conversation_store().conversation_file_name,
                awaken_round=summary_round,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "Agent[%s] reset 前收尾 summary 失败，仍继续 reset：%s: %s",
                self.name,
                type(exc).__name__,
                exc,
            )


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
        from src.core.init_prompts import (
            build_system_level_instruction_zh,
            build_user_level_instruction_zh,
        )

        if self._conversation_store is None:
            raise RuntimeError("conversation_store 未初始化，无法 reset_context")

        self._system_instruction = build_system_level_instruction_zh()
        self._user_instruction = build_user_level_instruction_zh()
        self._memory_manager_summary_awaken_count = 0
        self._memory_manager_judge_awaken_count = 0
        self._pause_requested = False
        self._paused = False

        conversation_store = ConversationStore(
            system_instruction=self._system_instruction,
            user_instruction=self._user_instruction,
        )
        self._conversation_store = conversation_store
        self._messages = [
            {"role": "system", "content": self._system_instruction},
            {"role": "user", "content": self._user_instruction},
        ]
        conversation_store.start_with_messages(messages=[])
        # reset-context 会创建一份全新的 conversation 文件；如果不初始化阈值，
        # 下一轮 run() 里会立刻再次触发 memory manager 唤醒（因为 used_percent 可能依然很高），
        # 造成同一轮里重复唤醒/甚至重复 reset 的“抖动”。
        context_limit = self._token_counter.context_window(self._model_config.model)
        current_tokens, _is_estimate = self._token_counter.count_messages_tokens(self._model_config.model, self._messages)
        used_percent = int(current_tokens * 100 / context_limit)
        current_threshold = (used_percent // MEMORY_MANAGER_CONTEXT_USED_THRESHOLD_STEP_PERCENT) * MEMORY_MANAGER_CONTEXT_USED_THRESHOLD_STEP_PERCENT
        conversation_store.update_memory_manager_last_triggered_threshold(last_triggered_threshold=current_threshold)
        self._persist_pause_state()
        self._notify_switch_conversation(messages=self._messages)
        self._on_resumed()

    async def run(self) -> dict[str, Any]:
        conversation_store = self._require_conversation_store()

        self._safe_drain_user_message_queue()
        if not conversation_store.has_persisted_conversation():
            # 显式校验：如果没有待处理的 user message，就不应该进入模型生成路径。
            # 否则会进入 _append_runtime_message -> ConversationStore.append_message，最终抛出更隐晦的异常。
            raise RuntimeError("conversation 尚未开始：没有待处理的 user message，请先 enqueue_user_message()")

        while True:
            logger.info(
                "Agent[%s].run：开始模型调用（messages=%s tools=%s paused=%s pause_requested=%s）",
                self.name,
                len(self._messages),
                len(self._tools),
                self._paused,
                self._pause_requested,
            )
            ai_msg_dict = await self._safe_stream(model_config=self._model_config,
                                                  messages=self._messages,
                                                  tools=self._tools,
                                                  on_ai_content_delta=self._on_ai_content_delta,
                                                  on_ai_reasoning_delta=self._on_ai_reasoning_delta,
                                                  on_ai_tool_call_started=self._on_ai_tool_call_started,
                                                  on_ai_tool_call_arguments_delta=self._on_ai_tool_call_arguments_delta,
                                                  on_ai_tool_call_finished=self._on_ai_tool_call_finished,
                                                  )
            # 这个判断条件对应 _safe_stream 中的：“agent被突然中断
            # 导致 message 数组最后一个可能是 AI message with tool call”
            # todo 这个_safe_stream应该能设计得更好一点？比如改成 _safe_steam_and_append?
            if ai_msg_dict is not self._messages[-1]:
                self._append_runtime_message(ai_msg_dict)
            if not ai_msg_dict.get("tool_calls"):
                # 即使本轮没有工具调用，也需要按上下文阈值唤醒 memory manager，
                # 否则“纯聊天”场景永远不会触发摘要/重置判断。
                await self._maybe_wake_memory_manager()
                if self._pause_requested:
                    # 为了让“暂停”在有pending user message的场景下也可靠生效：
                    # 即使本轮没有 tool_calls，只要本轮模型调用已经结束，
                    # 我们也要在回合边界暂停，阻止 runner 立刻进入下一轮模型调用。
                    self._pause_requested = False
                    self._paused = True
                    self._persist_pause_state()
                    logger.info("Agent[%s].run：在回合边界进入 paused", self.name)
                    self._on_paused()
                return ai_msg_dict

            logger.info(
                "Agent[%s].run：收到 tool_calls（n=%s）",
                self.name,
                len(ai_msg_dict.get("tool_calls") or []),
            )
            tool_messages = await execute_tool_calls(
                ai_msg_dict=ai_msg_dict,
                tools=self._tools,
                on_tool_result=self._on_tool_result,
            )
            for tool_message in tool_messages:
                self._append_runtime_message(tool_message)

            # 在这里maybe wake memory manager，最后一条msg是tool result msg
            # 这个格式是合法的，可以在这个基础上跑 summary、judge任务
            await self._maybe_wake_memory_manager()

            if self._pause_requested:
                # 用户点击暂停，可能是想看一会，然后恢复运行之前，还要输入一些内容，
                # 所以暂停检查点应该在 drain user msg 之前。
                # 同时必须在 tool_messages 已经 append/persist 且 memory manager 唤醒结束之后，
                # 否则会造成“用户看到了工具结果，但 memory manager 状态没有同步”的错觉。
                self._pause_requested = False
                self._paused = True
                self._persist_pause_state()
                logger.info("Agent[%s].run：在工具执行后进入 paused", self.name)
                self._on_paused()
                return ai_msg_dict

            # steer message 注入点。在执行完toolcall后注入最符合直觉
            # 另外注意，我们是在 memory manager reset-context 之后才注入，
            # 因为上下文越精简，ai表现越好，reset context的优先级应高于steer conversation
            self._safe_drain_user_message_queue()
            continue
