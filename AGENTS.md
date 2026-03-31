# 项目记忆（Project X）

目标：用“摘要 + 索引”的方式快速理解代码库，并能迅速定位“该改哪个文件”。

## 核心心智模型（先看这个）
- **核心抽象是 `Agent`**：`backend/src/core/agent.py` 对外暴露一个最小接口（排队 user message → `async run()` 生成 → 工具调用 → 持久化）；其他模块基本都在为它服务。
- **`WebSocketChatSession` 是适配层**：`backend/src/websocket_chat_session.py` 直接 `await Agent.run()`，并把回调投影成前端事件（assistant delta / tool card / committed 等）。
- **`ConversationStore` 是持久化层**：`backend/src/conversation_store.py` 把对话落地到 `~/.project-x/memories/originals/*.json`，并提供 list/detail 所需字段（`displayName`/`lastChatTime`）。
- **system/user instruction 由 prompts 构建**：`backend/src/prompts/builder.py` 会读取/确保 `~/.project-x/memories/summaries/main.md`，`reset_context` 会触发“新会话 + 重新加载指令”的编排。

数据流（大致）：
`frontend` → `/ws` → `WebSocketChatSession.submit_user_message()` → `Agent.enqueue_user_message()` → `await Agent.run()` → `await agent_turn.stream()` →（可选）`await execute_tool_calls()` → `ConversationStore.append_message()` → 事件经 `ChatEventProjector` 回前端  
历史会话：`/conversations` / `/conversations/{conversationId}` 读取 `ConversationStore`。

## 快速定位表（改功能先看这里）
- 想改“对外能力/时序/回调/持久化规则”：`backend/src/core/agent.py`
- 想改“模型流式/工具流式/工具执行规则（含 reset_context 特判）”：`backend/src/core/agent_turn.py`
- 想改“会话文件格式/displayName/lastChatTime/落盘时机”：`backend/src/conversation_store.py`
- 想改“WebSocket 事件长什么样/事件边界/assistant ↔ tool 拆分/reset.context 行为”：`backend/src/websocket_chat_session.py`
- 想改“HTTP API（会话列表/详情）或 WebSocket 路由”：`backend/src/web_app.py`
- 想改“模型选择/API key/Mock”：`backend/src/core/model_config.py`
- 想改“前端渲染/时间线/侧栏/输入框”：`frontend/src/App.tsx`、`frontend/src/features/chat/components/*`
- 想改“前端协议类型/校验”：`frontend/src/features/chat/protocol.ts`
- 想改“前端状态机/事件投影逻辑”：`frontend/src/features/chat/store.ts`
- 想改“WS 客户端连接/重连/发送/事件校验”：`frontend/src/features/chat/client.ts`
- 想改“会话 HTTP API + 历史转前端 items”：`frontend/src/features/chat/conversations.ts`

## 开发与运行（常用）
- 一键启动：根目录 `dev.sh`（前端 `npm run dev` + 后端 `PYTHONPATH=. uv run python main.py`）
- 后端入口：`backend/main.py`（`PROJECT_X_HOST`/`PROJECT_X_PORT`）
- 后端测试：在 `backend/` 下运行 `PYTHONPATH=. uv run --with pytest python -m pytest -q`（沙盒内若遇到 uv cache 写入失败，改为允许非沙盒执行）
- 前端开发代理：`frontend/vite.config.ts` 代理 `/healthz`、`/conversations`、`/ws` 到 `PROJECT_X_BACKEND_ORIGIN`（默认 `http://127.0.0.1:8000`）

## 后端（围绕 Agent 的三层）

### 1) `Agent`：对外接口与不变量（`backend/src/core/agent.py`）
对外接口（最常用的 5 个）：
- `new_conversation()`：开始新对话（写入 system/user instruction；但**不会**立即创建 conversation 文件）
- `resume_conversation(conversation_id=...)`：恢复历史对话（会用历史里的 system/user instruction 覆盖当前）
- `enqueue_user_message(frontend_msg_id=..., user_message=...)`：排队一条 user message（`frontend_msg_id` 由前端生成，用于 committed 回传）
- `has_pending_user_messages()`：是否还有排队消息
- `run()`：异步生成循环：drain 队列 → 调模型（流式回调）→（可选）执行工具 → 持久化 → 再 drain → 直到没有 tool_calls

关键不变量/约束：
- 调用者必须先 `new_conversation()` / `resume_conversation()`，再 `enqueue_user_message()` / `run()`
- conversation 文件创建时机：**首条 committed 的 user message 被 drain 进 `_messages` 时才落地**（避免“空会话文件”）
- `run()` 会显式拒绝“新会话尚未开始（未持久化首条 user message）就进入生成”的误用
- provider 兼容：deepseek 需要在发送下一条 user message 前去掉 `reasoning_content`（`backend/src/core/policies.py`）
- `reset_context`：工具执行阶段可能返回 `ResetContextDirective`，触发 `Agent._reset_context()` 新建会话并回调 `on_reset_context`

回调（适配层用来投影前端事件）：
- AI 流式：`on_ai_content_delta` / `on_ai_reasoning_delta`
- 工具流式：`on_ai_tool_call_started` / `on_ai_tool_call_arguments_delta` / `on_ai_tool_call_finished`
- 工具结果：`on_tool_result(tool_call_id, result_json_str)`
- 队列提交：`on_queued_user_msg_committed(frontend_msg_id=...)`
- 首次持久化：`on_conversation_persisted(conversation_id, display_name)`
- 重置上下文：`on_reset_context(conversation_id, display_name)`

### 2) 回合引擎与工具系统（`backend/src/core/agent_turn.py`）
- `stream()`：通过 `litellm.acompletion(..., stream=True)` 拉流（async），拼装最终 assistant message（OpenAI 风格 dict）
- 工具流式：会把 `tool_calls` delta 按 `index` 合并，分别触发 started/arguments.delta/finished 回调
- `execute_tool_calls()`：解析 JSON arguments → 分发到 `ToolSpec.handler` → 产出 `tool_messages`；并对 `reset_context` 做特判（不能与其他工具并发，且不会真正执行 handler）

内置工具：
- `backend/src/tools/bash.py`：`BASH_TOOL`（入参 pydantic 校验；`bash -lc` 执行；返回 stdout/stderr/returncode）
- `backend/src/tools/reset_context.py`：`RESET_CONTEXT_TOOL`（真实编排在 `Agent._reset_context()`；首次调用只返回 hint）

### 3) 持久化（`backend/src/conversation_store.py`）
- 落地目录：`~/.project-x/memories/originals/`（可用 `PROJECT_X_MEMORIES_ROOT` 覆盖根目录，见 `backend/src/commons.py`）
- `conversation_id`：文件名 `<coolname>-<UTC时间戳>.json`
- JSON 结构：`{ meta: { "display-name": str }, messages: [ {role, content, ..., meta:{timestamp}} ] }`
- `displayName`：默认取首条 committed 的 user message，最多 20 字符，超出补 `...`
- `lastChatTime`：取最后一条持久化消息的 `meta.timestamp`（UTC ISO），`GET /conversations` 用它倒序排序
- 给模型用的 runtime messages 会 strip 掉每条 message 的 `meta`

## 服务层：把 Agent 暴露给前端

### WebSocket 会话编排（`backend/src/websocket_chat_session.py`）
- 每个 WS 连接一个 `WebSocketChatSession`；直接 `await self._agent.run()`，不再做线程桥接
- `ChatEventProjector` 决定 assistant 卡片边界：遇到 tool start 会 close 当前 assistant，因此前端时间线呈 `assistant → tool → assistant`
- WS 支持 `/ws?conversationId=...` 以 resume 历史会话
- `reset_context`：先投影 `reset.context`，然后**由WebSocketChatSession直接推送**一条 `user.message.committed`（auto reminder），触发前端渲染出 auto reminder 文本

### HTTP + WS 路由（`backend/src/web_app.py`、`backend/main.py`）
- `GET /healthz`
- `GET /conversations`：会话列表（按 `lastChatTime` 倒序）
- `GET /conversations/{conversationId}`：会话详情（含历史 messages）
- `WS /ws`：收 `send_user_message` / `ping`（命令协议：`backend/src/web_protocol.py`）

## 前端（保持“简单单栏聊天”的约束）

### 核心文件
- 页面布局/交互：`frontend/src/App.tsx`（侧栏会话 + 主区时间线 + 底部输入；会话列表仅启动时拉取一次，持久化后通过事件 upsert）
- 协议类型：`frontend/src/features/chat/protocol.ts`（zod 校验；事件含 `reset.context`、`conversation.persisted` 等）
- Store：`frontend/src/features/chat/store.ts`（核心状态：`items[]`、`pendingUserMessages[]`、`isGenerating`、`activeConversationId`、`persistedConversation`）
- WS 客户端：`frontend/src/features/chat/client.ts`（切换会话会重连 `/ws?conversationId=...`；忽略过期 socket 事件以兼容 StrictMode）
- 历史会话：`frontend/src/features/chat/conversations.ts`（HTTP list/detail + 把后端历史 messages 投影成平铺 `ChatItem[]`）
- 组件：`frontend/src/features/chat/components/*`（assistant reasoning 默认展开；tool result 过长会折叠）

时间线模型（重要的简化）：
- 前端采用平铺 `user/assistant/tool` items，不表达“assistant 内嵌 tool 段落”；事件顺序决定 UI 顺序
- `zustand@5 + React 19` 注意：selector 不要返回临时新对象/新数组（避免 `Maximum update depth exceeded`）
- ESLint 启用了 `react-hooks/set-state-in-effect`：避免在 `useEffect` 回调体内同步调用 React `setState`，优先把更新放到订阅/异步回调里或改用外部 store

### 测试注意事项
- Playwright strict mode：`getByText()` 容易 strict violation，优先用更具体的 locator（例如 `getByRole('main')...`）
- Codex CLI 沙盒内禁止创建 socket（例如 uvicorn 绑定端口），跑 e2e 需要允许非沙盒执行

## 文档说明
- `docs/zh/draft-plans/`：早期草案/想法，内容不保证与当前实现一致
- `docs/zh/plans/`：实施时写下的更稳定计划文档（通常与当前实现更一致，但仍以代码为准）
- `docs/zh/code_explanations/`：教学/讲义（例如 `teach_backend_asyncio_basics.md`、`teach_frontend_store_basics.md`）

## 计划索引
- 2026-03-31：把 Agent 改为 async（移除 WebSocket 层 to_thread）：`docs/zh/plans/2026-03-31-agent-async.md`

## 供应链安全备忘
- LiteLLM 供应链投毒事件（2026-03-24）：受影响版本为 `litellm==1.82.7` 与 `litellm==1.82.8`（其中 `1.82.8` 包含会在 Python 启动时自动执行的恶意 `.pth`）。本项目当前 `backend/uv.lock` 锁定为 `litellm==1.82.0`，并在 `backend/pyproject.toml` 显式排除了 `1.82.7/1.82.8`。
