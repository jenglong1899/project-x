这个功能感觉过于复杂了，用 Log 记录就行了，见 2026-05-23-memory-manager-runner-logging.md ，所以这个计划目前还没有实现。

# 目标
在网页端把 memory manager 的 **summary** 和 **judge** 工作过程以“像聊天一样”的时间线展示出来：
- 每次被唤起时，自动新建 **两个 tab**：`summary` 与 `judge`
- tab 展示名：`{shortConversationId}-{awakenSeq}-{lane}`（lane 为 `summary` 或 `judge`）
  shortConversationId 取 conversationId 前 6 位（只用于展示，避免太长）
- 不自动切换到新 tab；用户仍停留在当前 worker 聊天 tab
- 不清空旧 tab（每次唤起都是新 tab，因此天然隔离）

# 关键约束 / 决策
- 直接使用 **conversation 文件名** 作为 `conversationId`
- summary/judge 的展示粒度与 worker 一致：assistant 的 `content` / `reasoning` 以及 `tool.*` 事件都要展示
- 复用现有事件结构，新增字段做分流，而不是新增一堆新事件类型

# 协议（后端 → 前端）
所有 ServerEvent 增加公共字段：
- `lane: 'worker' | 'summary' | 'judge'`
- `conversationId: string`（直接用 conversation 文件名，直接用 conversation 文件名；用于唯一键，不截断）
- `awakenSeq: number | null`
  - `lane='worker'` 时为 `null`
  - `lane in ('summary','judge')` 时为本次唤起序号（从 1 开始）

事件流分三条“逻辑时间线”：
```
worker lane:  现有聊天（items/pending/isGenerating/paused）
summary lane: memory summary runner 的 stream + tool 执行过程
judge lane:   memory judge runner 的 stream + tool 执行过程（实际上不会被执行）
```

# 后端实现思路
## 1) 在 Agent 唤起时绑定 awakenSeq
- `backend/src/core/agent.py` 在启动 `summary_task` 时：
  - 递增 `_memory_manager_awaken_count` 后得到 `awakenSeq`
  - 创建专用 callbacks（带 lane + awakenSeq + conversationId）
- `judge_task` 必须与同一轮唤起共享同一个 `awakenSeq`
  - 允许两者并发，但事件必须带同一 awakenSeq

## 2) Memory runner 透传 stream/tool 过程
- `backend/src/core/memory_manager.py`
  - 给 `MemoryManagerSummaryRunner.run()` / `MemoryManagerJudgeResetContextRunner.run()` 增加可选回调参数：
    - `on_ai_content_delta`
    - `on_ai_reasoning_delta`
    - `on_ai_tool_call_started/arguments_delta/finished`
    - `on_tool_result`
  - 内部调用 `stream()` / `execute_tool_calls()` 时不再使用 `noop`，改为使用上述回调

## 3) WebSocket projector 做 lane 标记（不互相污染状态）
- `backend/src/websocket_chat_session.py`
  - 提供一个 `make_lane_emitter(lane, conversationId, awakenSeq)`，在 emit 时自动附加这三个字段
  - 每条 lane 各自维护独立的 `ChatEventProjector`（避免 `_active_assistant_message_id` / tool 状态互相覆盖）
    - worker：现有 projector
    - summary/judge：每次唤起创建一个新的 projector 实例（其 emitter 会写死 lane/conversationId/awakenSeq）

## 4) conversationId 的来源
- 从  Agent 当前 conversation 文件名获取（需要在 `Agent` 暴露一个只读 getter）
- `conversation.switched` 事件也要带上 `conversationId`（用于 worker tab 命名/一致性）

# 前端实现思路
## 1) 协议扩展
- `frontend/src/features/chat/protocol.ts`
  - 给所有 server event schema 添加 `lane/conversationId/awakenSeq`

## 2) Store：从单时间线升级为多 tab
- `frontend/src/features/chat/store.ts`
  - 新增：
    - `tabs: { id, title, lane, conversationId, awakenSeq, items, pendingUserMessages, isGenerating, pauseRequested, isPaused }[]`
    - `activeTabId`（默认 worker）
  - `applyServerEvent()`：
    - 根据 `(lane, conversationId, awakenSeq)` 找到 tab；找不到就创建（summary/judge 自动新建）
    - 复用现有 `reduceServerEvent()` 逻辑，但作用域从“全局 state.items”改成“tab.items”
  - worker tab 继续支持输入、pause/resume；summary/judge tab 仅展示（不发送消息）

## 3) UI：真正的 tab（不是面板）
- `frontend/src/App.tsx`
  - 顶部增加 tab 条（worker 默认存在；summary/judge 由事件驱动出现）
  - 主区域渲染 `activeTab.items`，复用现有 bubble/card 组件
  - 发送框只在 `activeTab.lane === 'worker'` 时显示/可用

# 测试思路
- 后端：在 `backend/tests/` 增加/扩展 websocket session 单测，断言：
  - memory summary/judge 事件都带 lane/conversationId/awakenSeq
  - 同一轮唤起 summary/judge 的 awakenSeq 一致
- 前端：最少做一次手测路径：
  - 触发 tool result → 等待 memory manager 唤起 → 观察自动出现两个 tab 且内容流式增长

# 假设
- 允许在前端协议中暴露 conversation 文件名（用户已确认选 A）
