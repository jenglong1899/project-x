# 计划：把 Agent 改为 async（移除 WebSocket 层 to_thread）

日期：2026-03-31

## 背景与目标

改造前状态（已完成改造，本段用于说明动机）：
- `backend/src/core/agent.py` 的 `Agent.run()` 是同步函数。
- `backend/src/websocket_chat_session.py` 为了不阻塞事件循环，使用 `await asyncio.to_thread(self._agent.run)` 桥接。
- `backend/src/core/agent_turn.py` 通过 `litellm.completion(stream=True)` 同步拉流，并在 `execute_tool_calls()` 里同步执行工具（例如 `bash` 用 `subprocess.run`）。

完成后现状（当前代码）：
- `backend/src/core/agent.py`：`Agent.run()` 已改为 `async def run(...)`。
- `backend/src/websocket_chat_session.py`：已直接 `await self._agent.run()`，不再使用 `to_thread`，也不再需要线程锁/线程安全 emitter。
- `backend/src/core/agent_turn.py`：`stream()` / `execute_tool_calls()` 已改为 async，并使用 `litellm.acompletion(..., stream=True)` 拉流。
- `backend/src/tools/bash.py`：已改为 asyncio 子进程（`asyncio.create_subprocess_exec`）。

目标（本次要实现）：
- 将 Agent 主链路改为真正的异步执行：`Agent.run()` 变为 `async`，并且模型流式与工具执行不阻塞事件循环。
- `WebSocketChatSession` 不再使用 `asyncio.to_thread`，而是直接 `await agent.run()`。
- 保持对前端事件投影语义不变：assistant/tool 卡片边界、`reset.context` 与 auto reminder 的事件顺序等。

非目标（本次不做）：
- 不引入“多 Agent 编排器/调度器”抽象（但需要保证设计不会阻塞未来扩展）。
- 不改前端协议、不改 HTTP API、不改 conversation 文件格式。
- 不做工具并发（仍然按 tool_calls 顺序串行执行），也不新增 provider 限流策略（必要时后续独立加）。

## 主要风险（雷点）与规避策略

### 风险 1：只改 async 签名但内部仍阻塞
如果 `async def run()` 内仍调用阻塞函数（如 `subprocess.run`、同步网络拉流、长时间磁盘 IO），会卡住整个事件循环，导致所有 WebSocket 连接一起卡顿。

规避：
- 模型流式改用 `litellm.acompletion(stream=True)` + `async for chunk in ...`。
- `bash` 工具改用 `asyncio.create_subprocess_exec`，避免阻塞。
- 落盘仍是同步小文件写入（保持简单）；若后续出现明显阻塞，再把落盘封装为 `asyncio.to_thread` 或引入异步文件 IO（不在本次范围）。

### 风险 2：回调签名需要 await
目前回调（delta/tool 事件）都是同步函数；如果强行改成 async，会把大量代码变复杂（每次回调都要 await / create_task）。

策略：
- 回调协议保持同步（`__call__ -> None`），回调内部只做“轻量投影”（例如 `queue.put_nowait`），不做任何阻塞操作。

### 风险 3：工具 handler 变成 async 的连锁反应
`ToolSpec.handler` 目前是同步；改成 async 会影响：
- `execute_tool_calls` 的实现
- 测试里构造的 Fake 工具
- `reset_context` 特判逻辑

策略：
- 将 `ToolHandler` 统一改为 async callable（`await handler(...)`）。
- `reset_context` 仍然走特判，不执行 handler（行为保持一致）。

## 设计概览（自顶向下）

### 新的数据流（关键路径）

```
WebSocketChatSession.submit_user_message()
  -> Agent.enqueue_user_message()
  -> WebSocketChatSession._run_agent_until_idle()
       -> await Agent.run()
            -> drain user queue（同步、轻量）
            -> await agent_turn.stream(...)        # async 模型流式
            -> await agent_turn.execute_tool_calls # async 工具执行
            -> ConversationStore.append_message    # 同步落盘（短）
            -> 如触发 reset_context：Agent._reset_context()（同步编排，短）
```

### 对外接口与约束（保持最小惊讶）
- `Agent.new_conversation()` / `resume_conversation()` / `enqueue_user_message()` 保持同步。
- 仅把 `Agent.run()` 改为 `async def run(...)`。
- `WebSocketChatSession` 仍然在“生成期间”循环跑 Agent，直到没有 pending work。

## 具体改动范围（按文件）

### 1) `backend/src/core/agent_turn.py`
目标：把“模型流式”和“工具执行”变成 async。

改动点：
- 将 `stream(...)` 改为 `async def stream(...)`：
  - mock 分支：使用 `await asyncio.sleep(...)` 替代 `time.sleep(...)`。
  - 非 mock 分支：使用 `litellm.acompletion(..., stream=True)`。
  - 通过 `async for chunk in response_stream:` 处理 delta，逻辑尽量复用当前合并/回调触发方式。
- 将 `ToolHandler` 定义改为 async callable：
  - `class ToolHandler(Protocol): async def __call__(..., arguments: dict[str, Any]) -> Any: ...`
- 将 `execute_tool_calls(...)` 改为 `async def execute_tool_calls(...)`：
  - `reset_context` 特判逻辑保持不变（仍然不执行 handler）。
  - 其他工具：`tool_result = await tool_spec.handler(arguments=parsed_arguments)`。

验收标准：
- 现有的 tool_calls merge 行为、回调触发时机与顺序不改变。
- `reset_context` 并发限制的报错语义不改变。

### 2) `backend/src/tools/bash.py`
目标：避免阻塞事件循环。

改动点：
- 将 `run_bash` 改为 `async def run_bash(...)`，使用：
  - `proc = await asyncio.create_subprocess_exec("bash", "-lc", command, stdout=PIPE, stderr=PIPE)`
  - `stdout, stderr = await proc.communicate()`
- `BASH_TOOL.handler` 指向异步 handler。

验收标准：
- 输出结构保持 `{stdout, stderr, returncode}`。
- 输入校验（pydantic）与 JSON schema 输出保持一致。

### 3) `backend/src/tools/reset_context.py`
改动点：
- 将 handler 改为 async 以匹配新的 `ToolHandler` 类型（即使不会被执行，也保持类型一致）。

### 4) `backend/src/core/agent.py`
目标：把 orchestrator 循环变成 async，并使用新的 async `stream/execute_tool_calls`。

改动点：
- 将 `run(self) -> dict[str, Any]` 改为 `async def run(self) -> dict[str, Any]`。
- 将 `_safe_stream(...)` 改为 async（内部 `return await stream(...)`）。
- 将 `self._user_msg_queue` 从 `queue.Queue` 改为 `collections.deque`：
  - 原因：改成单线程事件循环后，不需要线程安全队列；deque 更轻量，且无需 await。
- 其他逻辑保持一致：
  - conversation 首条 user message 落盘时机不变。
  - `reset_context` 的“第一次调用只返回 hint”行为不变。

验收标准：
- 原有不变量继续成立（例如：未持久化首条 user message 不允许进入模型生成路径）。
- 事件回调触发顺序与当前语义一致（尤其是 committed、persisted、tool result）。

### 5) `backend/src/websocket_chat_session.py`
目标：移除 `to_thread`，并删掉为跨线程安全而存在的复杂度。

改动点：
- `AgentLike.run()` 改为 `async def run(...)`。
- 删除线程相关内容：
  - 移除 `threading`、`Lock`、`call_soon_threadsafe`、`_emit_from_any_thread`。
  - `_pending_user_contents` 不再需要锁（所有读写都在同一事件循环线程）。
- 事件投影发出方式：
  - projector 的 emitter 仍为同步函数（不能 await），内部使用 `self._outgoing_queue.put_nowait(event)`。
- `_run_agent_until_idle`：
  - 用 `await self._agent.run()` 替代 `await asyncio.to_thread(...)`。

验收标准：
- `agent.became.busy/agent.became.idle` 边界不变。
- tool 开始时会 close 当前 assistant message 的行为不变。
- `reset.context` + auto reminder 的事件顺序保持（reset 在前，auto user committed 在后，且早于任何新的 assistant delta）。

### 6) 测试改造（`backend/tests/*`）
目标：让测试与 async 版本对齐，覆盖关键行为。

改动点：
- `test_websocket_chat_session.py`
  - `FakeAgent.run()` 改为 `async def run(...)`；脚本执行仍为同步回调即可。
- `test_bash_tool.py`
  - 迁移为 `unittest.IsolatedAsyncioTestCase`，并 `await BASH_TOOL.handler(...)`。
  - mock `asyncio.create_subprocess_exec`，用假的 `proc.communicate()` 返回 stdout/stderr。
- `test_agent_callbacks.py`
  - `execute_tool_calls` 改为 await；测试函数改为 `IsolatedAsyncioTestCase` 或在 case 内运行事件循环。
  - 工具 handler（例如 echo/raw_text）改为 async 函数。

验收标准：
- 后端测试能通过：`cd backend && PYTHONPATH=. uv run --with pytest python -m pytest -q`。

## 实施顺序（最小化回滚成本）
1. 先改 `agent_turn.py` 为 async（包含 mock 分支），并把 `execute_tool_calls` 改为 async。
2. 再改 `tools/bash.py` / `tools/reset_context.py` 适配新的 async handler。
3. 再改 `agent.py`：`run` async、队列结构替换、调用 await 的 `stream/execute_tool_calls`。
4. 再改 `websocket_chat_session.py`：移除 `to_thread` 与线程安全 emitter/锁。
5. 最后改测试并跑全量后端测试。

## 自检（确保计划“不省略也不过细”）
- 自顶向下：先明确目标/非目标/风险，再给出设计，再落到文件级改动点与验收标准。
- 关键接口都写清楚了：`Agent.run`、`agent_turn.stream/execute_tool_calls`、`ToolSpec.handler`、`WebSocketChatSession` 的 emitter 行为。
- 没有写到“每一行怎么改”的程度，但足够让不同实现者写出相同结构的代码。
