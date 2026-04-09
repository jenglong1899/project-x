# 计划：抽取 AgentRunner（把“确保运行 + 防重入 + 跑到 idle”从 WebSocketChatSession 下沉到 core）

日期：2026-03-31

## 背景与目标

当前现状：
- `backend/src/websocket_chat_session.py` 在 `submit_user_message()` 里做了：
  - `agent.enqueue_user_message()`
  - 确保存在后台 `_runner_task`
  - 防止 `agent.run()` 重入
  - 在同一次 generation 内循环调用 `agent.run()`，直到没有 pending user message（跑到 idle）

问题：
- 这段“驱动 Agent 的通用编排”并不是 WebSocket 独有逻辑；未来如果出现其他适配层（例如 HTTP SSE、CLI、worker），会重复实现同一套 runner 逻辑。
- 直接把后台 task 生命周期塞进 `Agent` 本体会让 `Agent` 从“纯状态机 + 回调”变成“自带 actor/任务调度器”，耦合过强且不利于测试与复用。

本计划目标：
- 新增一个 core 层的 `AgentRunner`，承载“确保运行 + 防重入 + 跑到 idle + 错误处理”的编排。
- `WebSocketChatSession` 只保留“协议投影 + 关闭语义 + pending content 映射”等 WS 特有内容。
- 不改变前端事件语义（generation 边界、assistant/tool 卡片边界、`reset.context` + auto reminder 的顺序等）。

## 非目标（本次不做）

- 不引入/实现 `Agent.submit_user_msg_and_ensure_running()`；`enqueue_user_message()` 与 `run()` 仍然保留并继续作为底层接口存在。
- 不改变 conversation 持久化规则、不改 reset_context 行为、不改前端协议与事件类型。
- 不实现“强制取消正在进行的 agent.run()”（仍保持当前：session close 后不再发送事件，但 agent.run 允许自然结束）。

## 设计概览（自顶向下）

### 核心职责划分

```
WebSocketChatSession
  - 把 WS 入站 user message 变成 enqueue
  - 维护 userMessageId -> content 映射（用于 committed 事件补 content）
  - 负责事件投影（ChatEventProjector + emit）
  - 负责 close：终止对外事件流（outgoing_queue 塞 None）
  - 负责 reset_context 的 WS 特殊投影（reset.context + auto reminder）

AgentRunner（新）
  - 防重入：同一时间最多一个 runner task 在跑
  - 跑到 idle：循环 await agent.run()，直到 agent.has_pending_user_messages() 为 False
  - generation 边界：提供 started/completed 钩子（由调用方决定具体投影）
  - 错误处理：捕获异常并回调 on_error（由调用方决定如何对外呈现）
  - close 语义：通过 is_closed 回调在合适的边界退出（不强制 cancel agent.run）
```

### 关键语义（必须保持不变）

1) “一次 generation”定义不变：
- 当 session 从 idle 进入 generating 时发 `generation.started`。
- 同一次 generating 期间，多个 user message 可能被 drain 并得到多次 assistant 输出，但只发一对 started/completed。
- 当跑到 idle 或发生错误时发 `generation.completed`（并 close 未完成的 assistant message/tool 状态）。

2) runner 不重入：
- 在 runner 运行中再次提交消息，只会被 enqueue，不会启动第二个 runner task。

3) close 行为不变：
- `WebSocketChatSession.close()` 后立即向 `outgoing_queue` 塞 `None`，用于结束前端消费。
- close 不强制中断正在进行的 `agent.run()`；但 emitter 会因为 `_closed` 而丢弃后续事件。

### AgentRunner API（建议）

文件：`backend/src/core/agent_runner.py`

最小 Agent 协议（runner 只关心 run 与 pending）：

- `async def run(self) -> dict[str, Any]`
- `def has_pending_user_messages(self) -> bool`

`AgentRunner` 构造参数：
- `agent`: 满足最小协议的对象
- `is_closed: Callable[[], bool]`：由调用方提供（例如 session 的 `_closed`）
- `on_agent_became_busy: Callable[[], None] | None`
- `on_agent_became_idle: Callable[[], None] | None`
- `on_agent_turn_completed: Callable[[], None] | None`：每次 `await agent.run()` 返回后调用（保持当前 `projector.on_agent_turn_completed()` 语义）
- `on_error: Callable[[Exception], None] | None`：runner 捕获异常后回调

核心方法：
- `def ensure_running(self) -> None`：如果当前没有 runner task（或已结束），创建一个后台 task 去跑到 idle。
- （可选）`def is_running(self) -> bool`：便于调试与测试。

### 运行流程（伪代码）

```
def ensure_running():
  if task is None or task.done():
    task = asyncio.create_task(_run_until_idle())

async def _run_until_idle():
  on_agent_became_busy()
  try:
    while True:
      await agent.run()
      on_agent_turn_completed()
      if is_closed(): return
      if not agent.has_pending_user_messages(): return
  except Exception as exc:
    on_error(exc)
  finally:
    on_agent_became_idle()
    task = None
```

说明：这段逻辑与当前 `WebSocketChatSession._run_agent_until_idle()` 语义应完全一致，只是把状态与钩子抽出。

## 具体改动范围（按文件）

### 1) 新增 `backend/src/core/agent_runner.py`

- 定义 `RunnableAgent(Protocol)` 与 `AgentRunner`。
- `AgentRunner` 内部持有 `_task: asyncio.Task[None] | None`。
- 仅使用 `asyncio.create_task`（不依赖外部传入 loop）。
- 保持回调为同步函数（不引入 await 回调）。

验收标准：
- `ensure_running()` 在 runner 运行中被多次调用也不会创建多个 task。
- agent.run 抛异常时：会调用 `on_error`，并仍然调用 `on_generation_completed`。

### 2) 改造 `backend/src/websocket_chat_session.py`

- 引入并持有 `self._agent_runner: AgentRunner`（或同名字段）。
- `submit_user_message()`：enqueue 后调用 `self._agent_runner.ensure_running()`，移除 `_runner_task` 与 `_run_agent_until_idle()`。
- 错误处理：将当前 `_run_agent_until_idle` 里的 try/except 行为迁移到 runner 的 `on_error` 回调中，保持对外事件一致：
  - 日志：`logger.exception(...)`
  - 对外发 `{type: "error", code: "agent_run_failed", message: str(exc)}`
- `generation.started/completed`、`on_agent_run_completed` 继续通过 projector 钩子实现，确保事件边界不变。

验收标准：
- `backend/tests/test_websocket_chat_session.py` 全部通过且无需改动测试期望（事件序列不变）。

### 3) 测试策略

最小策略（必做）：
- 仅跑现有后端测试，确保行为不变：`cd backend && PYTHONPATH=. uv run --with pytest python -m pytest -q`

增强策略（可选，视实现复杂度决定是否加）：
- 新增 `backend/tests/test_agent_runner.py`，用 FakeAgent 验证：
  - 同一轮运行中多次 `ensure_running()` 不会启动多个 task。
  - agent.run 抛异常时仍然会触发 completed 钩子。

## 实施顺序（最小化风险）

1. 新增 `core/agent_runner.py`（先不改任何调用方）。
2. 将 `WebSocketChatSession` 的 runner 逻辑替换为 `AgentRunner`，保证事件序列不变。
3. 跑后端测试验证。
4. （可选）补充 `test_agent_runner.py`，然后再跑一次测试。

## 自检（计划质量）

- 自顶向下：从职责与语义出发，再落到 API、伪代码、文件级改动与验收标准。
- 不省略：明确了 generation 边界、close 语义、错误处理与不重入要求。
- 不过细：没有落到每个函数具体实现细节，但足够让不同实现者写出基本一致的结构与行为。
