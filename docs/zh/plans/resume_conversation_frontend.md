# 打通 Resume Conversation（后端 ↔ 前端）计划

## 1. 这个需求是否值得做？可能的雷点

**值得做**：后端 `Agent.resume_conversation()` 已经能恢复历史，但前端目前缺少“会话列表/切换会话”能力，用户无法选择并继续某个历史会话，导致持久化对用户不可见。

**主要雷点**
- **协议变更成本**：要做会话列表/切换会话，前后端协议必然要新增 command/event；前端 `safeParseServerEvent()` 对未知 `type` 会报错，所以必须同步改前端 schema。
- **历史渲染的最小信息**：前端只需要“可渲染的卡片数据”，这些卡片 ID 可以由前端运行时随机生成，不必持久化；但需要能正确重建 tool 卡（按 `tool_call_id` 关联）。
- **切换会话的并发约束**：当存在排队中的 user message（前端 `pendingUserMessages` 非空）或正在生成（`isGenerating=true`）时，**禁止切换会话**；否则容易出现“消息发到旧会话/新会话不确定”的竞态。

## 2. 推荐方案（面向“会话列表 + 切换会话”）

### 2.1 总体思路
- **新增“会话列表”HTTP API**：`GET /conversations` 返回 `{conversationId, displayName, lastChatTime}` 列表，前端侧栏“刷新列表”可用，并按 `lastChatTime` 做“最近会话”排序。
- **新增“会话详情”HTTP API**：`GET /conversations/{conversationId}` 返回历史 messages（不含路径，按现有 `ConversationStore` 校验）。
- **前端一次性渲染历史**：拿到历史 messages 后在前端转成 `ChatItem[]`，渲染用到的 `id` 全部前端运行时随机生成即可（不需要持久化）。
- **切换会话 = resume conversation**：
  - 前端点击侧栏会话：先用 HTTP 拉取并渲染历史，再断开并重连 WebSocket。
  - WebSocket 支持 query：`/ws?conversationId=...`，后端在连接建立时 `agent.resume_conversation()`，使后续新消息接在同一份 conversation 文件上。
    - 说明：如果不在连接时把 `conversationId` 传给后端，那么后端无法知道“这条 WS 连接后续应该 append 到哪一个会话文件”，除非再引入一个 `switch_conversation` 之类的 WS command。因此在“断开重连实现切换”的方案下，query 是最简单、最直接的传参方式。
- **conversationId 通知**：新会话在“首条 user message 持久化”后，后端主动发送一个事件告诉前端（比在 committed 里塞字段更直观）：
  - 事件示例：`{ type: "conversation.persisted", conversationId, displayName }`
  - 这样前端能立刻把“当前会话”标记为 active，并在侧栏里高亮。

### 2.2 数据流（ASCII）

```text
Browser                      Backend(HTTP)                 Backend(WebSocket)
  |  GET /conversations            |                               |
  |------------------------------->|                               |
  |<-------------------------------|  list                          |
  |
  |  GET /conversations/abc.json   |                               |
  |------------------------------->|                               |
  |<-------------------------------|  messages(history)             |
  |  前端一次性渲染卡片             |                               |
  |
  |  connect /ws?conversationId=abc.json                            |
  |--------------------------------------------------------------->|
  |        WebSocketChatSession: agent.resume_conversation()        |
  |
  |  send_user_message(userMessageId=uuid, content="hi")            |
  |--------------------------------------------------------------->|
  |              Agent.run() 流式回调 -> projector                   |
  |<---------------------------------------------------------------|  generation.started
  |<---------------------------------------------------------------|  user.message.committed
  |<---------------------------------------------------------------|  assistant/tool... streaming events
  |<---------------------------------------------------------------|  generation.completed
```

### 2.3 后端改动点（会新增少量 event/command）

1) `backend/src/web_app.py`
- 新增路由：
  - `GET /conversations`
  - `GET /conversations/{conversationId}`
- WebSocket：
  - 从 query 读取 `conversationId`
  - 传入 `WebSocketChatSession(loop=..., conversation_id=...)`（内部变量用 snake_case 即可）
  - resume 失败：给前端发 `error`（`code="conversation_resume_failed"`），然后 close（前端可重连/新建）

2) `backend/src/websocket_chat_session.py`
- `WebSocketChatSession.__init__` 支持可选 `conversation_id: str | None`
  - 有值：`agent.resume_conversation(conversation_id=...)`
  - 无值：`agent.new_conversation()`
- 当后端在“首条 user message 持久化”获得 `conversationId` 时，发送一次：
  - `conversation.id` 事件（含 `conversationId`、`displayName`）

3) `backend/src/core/agent.py`
- 增加只读能力供 `WebSocketChatSession` 使用：
  - `get_conversation_id() -> str | None`
  - `get_display_name() -> str | None`（从 store.meta 拿，或由首条 user 截断得到）

4) `backend/tests/*`
- `test_websocket_chat_session.py`：
  - 新增：当首条 user message 持久化后会发 `conversation.id`
- 新增 API 测试（建议直接测 Starlette app）：
  - `GET /conversations` 返回排序稳定、字段齐全
  - `GET /conversations/{id}` 能拿到 messages，且对非法 id 返回 4xx

### 2.4 前端改动点

1) `frontend/src/features/chat/protocol.ts`
- 新增 server event：`conversation.id`
- 不新增 `switch_conversation`：切换会话统一用“断开重连 + ws query resume”

2) `frontend/src/features/chat/store.ts`
- 增加状态：`activeConversationId: string | null`
- 处理 `conversation.id`：更新 `activeConversationId`

3) `frontend/src/features/chat/client.ts`
- `ChatClient.connect({ conversationId?: string })`（或允许更新 options）
- `resolveWebSocketUrl()` 若存在 `conversationId`，拼到 query

4) `frontend/src/App.tsx` + `ChatSidebar`
- 页面打开默认新对话：`chatClient.connect()` 不带 `conversationId`
- 启用侧栏：
  - “刷新列表”：HTTP 拉 `GET /conversations`
  - 点击某条会话：HTTP 拉详情并渲染 -> `chatClient.disconnect()` -> `useChatStore.reset()` -> `chatClient.connect({conversationId})`
  - “新会话”：清空 UI 并重连不带 `conversationId`
  - **切换禁用规则**：当 `pendingUserMessages.length > 0` 或 `isGenerating=true` 时：
    - 会话条目与“新会话”按钮置灰（或点击提示“请等待当前消息发送/生成完成后再切换”）
    - “刷新列表”可以保留可用（只读）

> 这里把“会话列表/切换会话”作为第一阶段的一部分，因为你已明确要求必须做上，且“resume conversation = 切换会话”。

## 3. 测试与验收（包含手工步骤 + 可选 e2e）

### 3.1 后端自动化测试（pytest）
- Web API：list/detail 的返回与边界校验
- WebSocket：首条持久化后会发 `conversation.id`；切换会话靠“断开重连 + query resume”即可覆盖

### 3.2 前端手工验收
1. `./dev.sh` 启动
2. 发送 1~2 条消息（最好触发一次工具调用）
3. 点击“刷新列表”看到历史会话
4. 点击某条历史会话：
   - 历史卡片一次性渲染
   - 继续发送消息会接续同一会话（不新开文件）
5. 点击“新会话”：
   - UI 清空
   - 后续消息走新的会话文件

### 3.3 可选：Playwright e2e
- 引入 Playwright（你已确认可以联网下载浏览器）后，新增 e2e 用例覆盖：
  - 新建会话并发送消息 -> 侧栏出现新会话
  - 切换到历史会话 -> 历史一次性渲染 -> 继续发送消息能接续同一会话（不是新开文件）
  - 在 `isGenerating=true` 或存在 pending 时，切换按钮被禁用

## 4. 需要你确认的关键问题（决定实现边界）

1) “切换会话”的实现方式你更偏好哪种？
- **断开重连**（推荐）：点击会话 -> 重连 `/ws?conversationId=...`，后端在握手阶段 resume。
- **不重连**：在同一个 WS 上发 `switch_conversation` command，让后端切 agent/store（实现更复杂）。

2) 会话列表/详情你希望走哪里？
- **HTTP API**（推荐）：`GET /conversations` + `GET /conversations/{id}`，前端更直观。
- **WebSocket command/event**：全部走 ws（协议会更“纯”，但实现与测试更绕）。
