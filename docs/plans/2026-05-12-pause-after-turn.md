# Pause（在回合边界暂停，支持“发消息自动解除”）

## 需求（最终语义）
- 用户点击“暂停”不会立刻中断当前执行；只会在**回合边界**暂停（保证状态一致、结果已落盘）。
- 回合边界定义：
  - 若本轮产生了 `tool_calls`：在**这一轮全部 tool_calls 执行完**（tool 结果已 append/persist，memory manager 已唤醒完）后暂停。
  - 若本轮没有 `tool_calls`：在**本轮模型调用结束**（assistant message 已 append/persist）后暂停。
- 暂停后如果用户又发送了消息：**自动解除暂停并继续运行**（不要求点“恢复”）。
- 需要 UI 按钮：暂停 / 恢复；并能展示“已请求暂停但尚未生效”的状态。

为什么“无 tool_calls 也要能暂停”？
- `AgentController` 会在 `Agent.run()` 返回后检查 `has_pending_work()`；如果队列里还有消息（用户连发、或前端短时间发了多条），即使本轮没 tool_calls，controller 也会立刻进入下一轮模型调用。
- 因此暂停必须能在“无工具的回合边界”生效，否则多消息场景下暂停会失效。
- 这点必须写进 `backend/src/core/agent.py` 的中文注释，避免未来误删/误改。

非目标：
- 不做“强制中断正在执行的 tool handler”（那是 cancel/kill 语义，不是 pause）。
- 不做“跨会话文件（conversation segment）继承暂停”（默认只影响当前最新的 conversation 文件；reset-context/new segment 后是否继承另行讨论）。

## 抽象与状态机
Agent 内维护两个标志位：
- `pause_requested: bool`：用户已经点击暂停，等待在下一次回合边界生效。
- `paused: bool`：已暂停；不会自动进入下一轮模型调用，直到解除。

持久化要求（跨标签页/后端也认）：
- 将 `pause_requested/paused` 持久化到 `ConversationStore` 的 `meta` 中（不写入 messages）。
- 新 WebSocket 连接恢复最近 conversation 时，需要从 meta 恢复暂停状态，并向前端补发一次 `agent.pause.requested` / `agent.paused`（用于 UI 对齐）。

状态转换（只画关键边）：
```
running --click pause--> pause_requested
pause_requested --到达回合边界--> paused
paused --click resume--> running
paused --用户发送消息--> running   （自动解除）
```

## 协议（前后端）
### Client → Server（WebSocket commands）
- `{ type: "request_pause" }`
- `{ type: "resume" }`

### Server → Client（events）
- `agent.pause.requested`：pause_requested=true（请求已记录，未必已暂停）
- `agent.paused`：paused=true（已在回合边界生效）
- `agent.resumed`：paused=false 且 pause_requested=false（显式恢复或“发消息自动解除”）

## 后端实现思路（自顶向下）
### 1) `web_protocol.py` + `web_app.py`
- 扩展 `ClientCommand` 支持 `request_pause` / `resume`。
- WS loop match 新 command，调用 `WebSocketChatSession` 对应方法。

### 2) `WebSocketChatSession`
- 增加 `submit_pause_request()` / `submit_resume()`，转发到 `AgentController`。
- 在调用后通过 projector emit 对应事件（requested/paused/resumed）。

### 3) `AgentController`
- 增加 `request_pause()` / `resume()`，内部转发到 agent，并负责：
  - `resume()` 后调用 `_ensure_running()`（让恢复能继续跑）。
  - `submit_user_message()`：先 enqueue；若 agent 当前 `is_paused()==True`，则先 `resume()` 并 emit `agent.resumed`，再 `_ensure_running()`。

说明：解除暂停的触发点放在 controller 而非纯前端，确保“发消息自动解除”在所有适配层都一致。

### 4) `AgentBase` / `Agent`
- `AgentBase` 新增抽象：
  - `request_pause() -> None`
  - `resume() -> None`
  - `is_paused() -> bool`
- `backend/src/core/agent.py`
  - `enqueue_user_message()`：如果 `paused==True`，自动 `resume()`（双保险；防止未来绕过 controller 直接调用 agent）。
  - `run()` 增加两个“回合边界检查点”：
    1) **无 tool_calls 分支**：assistant message append/persist 后，若 `pause_requested==True`，设置 `paused=True` 并清掉 request，然后 `return` 让 controller idle。
    2) **有 tool_calls 分支**：tool_messages append/persist + `await _maybe_wake_memory_manager()` 后（你原来的 TODO 位置），若 `pause_requested==True`，设置 `paused=True` 并清掉 request，然后 `return`。

注意：
- 不在 `run()` 开头用 `paused` 短路返回（避免返回“伪消息”污染持久化/回调语义）。
- 不需要 `last_turn_executed_tools` 之类局部变量：分支结构本身就能表达是否有工具。

### 5) `ConversationStore`（meta 持久化）
- `backend/src/conversation_store.py`
  - 在 `meta` 下新增 pause 状态，例如：`meta.pause = { "requested": bool, "paused": bool }`。
  - load 时从 meta 解析恢复；提供 `update_pause_state()` 写回 JSON（若已落盘）。
  - Agent 在 `request_pause()` / `resume()` / 进入 paused 时都要调用 `update_pause_state()`，保证跨标签页一致。

## 前端实现思路
- `frontend/src/features/chat/protocol.ts`：加 2 个 command + 3 个 event schema。
- `frontend/src/features/chat/client.ts`：新增 `requestPause()` / `resume()` 方法（发 WS command）。
- `frontend/src/features/chat/store.ts`：
  - 新增状态：`pauseRequested: boolean`、`isPaused: boolean`。
  - reducer 处理三种新事件，驱动按钮文案与可用性。
- UI（大概率在 `frontend/src/App.tsx` 或现有控制区组件）：
  - `pauseRequested=false && isPaused=false`：显示“暂停”
  - `pauseRequested=true && isPaused=false`：显示“等待暂停生效…” + “取消暂停(恢复)”（发 resume）
  - `isPaused=true`：显示“已暂停” + “恢复”

## 测试思路（后端优先）
- 后端（pytest）：
  1) 多消息场景：在 `pause_requested=true` 时，第一轮无 tool_calls，但队列里还有消息 → 应该在第一轮回合边界暂停，不进入第二轮模型调用。
  2) 有工具场景：pause_requested=true，执行完 tool_calls 后暂停，且 tool 结果已持久化。
  3) paused 后发送消息：自动解除暂停并继续运行（验证 controller 的 submit_user_message 行为）。
  4) 持久化：进入 pause_requested/paused 后落盘 meta；新建 session 恢复最近 conversation 时能恢复状态并 emit 对应事件。
- 前端：至少做手动验证清单；若已有前端测试基础，再补 store reducer 单测覆盖三事件。
