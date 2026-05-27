# 项目记忆

本文档是对代码库的一种摘要，目的是为了帮助 Agent 建立对代码库的理解，并不代表 Agent 不需要去阅读摘要所对应的代码文件，因为 Agent 有可能不知道“自己不知道”，比如 Agent 在实现某个功能的时候，做了某个兜底，但是由于没有看全代码，不知道原本的代码中已经有兜底了（不知道自己不知道），导致代码重复。

## 核心心智模型

本 Agent 系统的重点是记忆机制，有 worker 和 memory manager 两个角色，任务执行和记忆分离，从而减轻 AI 的注意力负担，达到更好的工作效果和记忆效果。

上下文每增长 3%，系统就从 worker 中 fork 两个 memory manager 出来异步工作，一个负责总结，一个负责决定是否要重置上下文。（等模型决定重置后再统一总结会导致遗漏）

- **核心抽象是 `Agent`**：`backend/src/core/agent.py` 对外暴露一个最小接口（排队 user message → `async run()` 生成 → 工具调用 → 持久化）；其他模块基本都在为它服务。
- **`AgentRunner` 是驱动层**：`backend/src/core/agent_runner.py` 负责“提交消息 + 确保后台运行 + 防重入 + 跑到 idle”，适配层（如 WebSocket）只和它交互，避免直接操作 `Agent`。
- **`WebSocketChatSession` 是适配层**：`backend/src/websocket_chat_session.py` 通过 `AgentRunner` 驱动 agent（busy/idle/turn 完成回调），并把回调投影成前端事件（assistant delta / tool card / committed 等）。
- **`ConversationStore` 是持久化层**：`backend/src/conversation_store.py` 把对话落地到 `~/.project-x/memories/originals/*.json`，并负责追加消息与恢复历史 messages。
- **Memory Manager 是双 runner**：触发点在 `Agent._maybe_wake_memory_manager()`；`summary runner` 维护 `~/.project-x/memories/summaries/MAIN.md` 等记忆文档，`judge runner` 只判断是否 reset-context（两者实现见 `backend/src/core/memory_manager.py`）。
- **reset-context 的关键约束**：当 judge 判定 reset 时必须先等待 in-flight summary 结束；reset 后会尽量保留 worker 最近 10 条消息且第一条必须是 `assistant`（落盘通过 `ConversationStore.start_with_messages()`）。

数据流（大致）：
`frontend` → `/ws` → `WebSocketChatSession.submit_user_message()` → `Agent.enqueue_user_message()` → `await Agent.run()` → `await agent_turn.stream()` →（可选）`await execute_tool_calls()` → `ConversationStore.append_message()` → 事件经 `ChatEventProjector` 回前端  
备注：当前产品形态没有“会话列表/切换/详情”的 HTTP API；前端只走 `/ws`。后端在连接建立时自动恢复最近的 conversation（没有历史文件则新建一个 segment）。

## 快速定位表（改功能先看这里）
- 想改“对外能力/时序/回调/持久化规则”：`backend/src/core/agent.py`
- 想改“模型流式/工具流式/工具执行规则（含 reset_context 特判）”：`backend/src/core/agent_turn.py`
- 想改“内置工具/共享工具状态（bash cwd、read_file 等）”：`backend/src/tools/*`
- 想改“会话文件格式/落盘时机”：`backend/src/conversation_store.py`
- 想改“WebSocket 事件长什么样/事件边界/assistant ↔ tool 拆分/conversation.switched 行为”：`backend/src/websocket_chat_session.py`
- 想改“HTTP API（会话列表/详情）或 WebSocket 路由”：`backend/src/web_app.py`
- 想改“模型选择/API key/Mock”：`backend/src/core/model_config.py`
- 想改“前端渲染/时间线/侧栏/输入框”：`frontend/src/App.tsx`、`frontend/src/features/chat/components/*`
- 想改“前端协议类型/校验”：`frontend/src/features/chat/protocol.ts`
- 想改“前端状态机/事件投影逻辑”：`frontend/src/features/chat/store.ts`
- 想改“WS 客户端连接/重连/发送/事件校验”：`frontend/src/features/chat/client.ts`

## 开发与运行（常用）
- 一键启动：根目录 `dev.sh`（前端 `npm run dev` + 后端 `PYTHONPATH=. uv run python main.py`）
- 后端入口：`backend/main.py`（`PROJECT_X_HOST`/`PROJECT_X_PORT`）
- 后端测试：在 `backend/` 下直接运行 `uv run pytest -q`；指定测试文件也用 `uv run pytest -q tests/...`，不需要写 `PYTHONPATH=.`
- 前端开发代理：`frontend/vite.config.ts` 代理 `/healthz`、`/ws` 到 `PROJECT_X_BACKEND_ORIGIN`（默认 `http://127.0.0.1:8000`）
- 回调约定：可选回调参数如果允许为 `None`，初始化时用 `backend/src/commons.py` 里的 `noop` 替代，避免到处写 `if callback is None`。

### 环境变量速查（最常用）
- `PROJECT_X_MODEL_CONFIG`：模型选择（`openai-codex`/`deepseek-v4-pro`/`deepseek-v4-flash`/`qwen3.5-plus`/`qwen3.5-flash`/`mock`）；默认 `deepseek-v4-pro`
  - 选 openai-codex: 需要在本机已登陆codex cli
  - 选 deepseek：需要 `DEEPSEEK_API_KEY`
  - 选 qwen：需要 `DASHSCOPE_API_KEY`
  - 选 mock：不需要外部 API key；可用 `PROJECT_X_MOCK_MODEL_DELAY_MS` 模拟延迟
- `PROJECT_X_MEMORIES_ROOT`：覆盖 `~/.project-x/memories`（包含 `originals/`、`summaries/`、`logs/`）
- `PROJECT_X_HOST` / `PROJECT_X_PORT`：后端监听地址与端口
- `PROJECT_X_BACKEND_ORIGIN`：前端 Vite 代理目标（默认 `http://127.0.0.1:8000`）
- `PROJECT_X_HF_CACHE_DIR`：tokenizer/HF 缓存目录（主要给精确 token 统计预留；当前 `TokenCounter` 默认走字符估算）

## 后端（围绕 Agent 的三层）

### 1) `Agent`：对外接口与不变量（`backend/src/core/agent.py`）
对外接口（最常用的 5 个）：
- `start_conversation()`：连接建立时调用；若 `~/.project-x/memories/originals/` 下存在历史文件则自动恢复“最新一份”，否则初始化一个新 segment（仅把 system/user instruction 放进内存，不会立刻落盘）
- `enqueue_user_message(frontend_msg_id=..., user_message=...)`：排队一条 user message（`frontend_msg_id` 由前端生成，用于 committed 回传；若当前处于 paused/pause_requested，会先自动 `resume()`）
- `run()`：异步生成循环：drain 队列 → 调模型（流式回调）→（可选）执行工具 → 持久化 → 再 drain → 直到没有 tool_calls 或命中暂停检查点
- `drive_decision()`：runner 是否应该自动继续调用 `run()`（把 pause gate / not_started / backlog 统一封装）
- `request_pause()` / `resume()`：用户侧“暂停/恢复”；暂停的语义是“在回合边界停住，阻止 runner 自动推进下一轮”
  - “paused”：定义为 **Runner 不会自动调用 `Agent.run()`**（它是一个 gate，而不是 backlog 的一部分）。
  - “backlog”：不考虑 pause 时，调用 `run()` 是否能推进状态机（排队 user msg / assistant(tool_calls) 需执行工具 / tool message 欠 follow-up assistant）。
  - “not_started”：conversation 未开始且无队列，用于显式解释为何不该跑（避免隐式约束）。

关键不变量/约束：
- conversation 文件创建时机：**首条 committed 的 user message 被 drain 进 `_messages` 时才落地**（避免“空会话文件”）
- `run()` 会显式拒绝“conversation 尚未开始（未持久化首条 user message）就进入生成”的误用
- `reset_context`：工具执行阶段可能返回重置指令（见 `backend/src/tools/reset_context.py` 与 `backend/src/core/agent_turn.py` 的特判）；真正的 reset 会在 Agent 内部重建会话并触发 `conversation.switched`

回调（适配层用来投影前端事件）：
- AI 流式：`on_ai_content_delta` / `on_ai_reasoning_delta`
- 工具流式：`on_ai_tool_call_started` / `on_ai_tool_call_arguments_delta` / `on_ai_tool_call_finished`
- 工具结果：`on_tool_result(tool_call_id, result_json_str)`
- 队列提交：`on_queued_user_msg_committed(frontend_msg_id=...)`
- 重置上下文：`on_reset_context(conversation_file_name)`

### 2) 回合引擎与工具系统（`backend/src/core/agent_turn.py`）
- `stream()`：通过 `litellm.acompletion(..., stream=True)` 拉流（async），拼装最终 assistant message（OpenAI 风格 dict）
- 工具流式：会把 `tool_calls` delta 按 `index` 合并，分别触发 started/arguments.delta/finished 回调
- `execute_tool_calls()`：解析 JSON arguments → 分发到 `Tool.handler` → 产出 `tool_messages`；并对 `reset_context` 做特判（不能与其他工具并发，且不会真正执行 handler）

内置工具：
（默认 Agent 工具列表包含 `bash` 和 `read_file`，二者通过每个 Agent 独立的 `CwdState` 共享 cwd。）
- `backend/src/tools/bash.py`：`create_bash_tool()`（入参 pydantic 校验；`bash -lc` 执行；返回 stdout/stderr/returncode；会更新共享 cwd）
- `backend/src/tools/read_file.py`：`create_read_file_tool()`（读取文件片段，默认显示 `nl -ba` 风格行号，按完整行应用 `max_chars` 截断；`end` 表示实际返回内容的最后一行；若第一行就超过 `max_chars`，则返回 `end=null` 且 `truncated=true`，方便从 `end + 1` 续读）
- `backend/src/tools/replace_text.py`：`create_replace_text_tool()`（支持 `literal/regex`；regex 为 Python `re` 语法，`DOTALL | MULTILINE`；替换文本里用 `$!1/$!2...` 引用捕获组；默认不允许多处匹配，避免误替换）
- `backend/src/tools/insert_text.py`：`create_insert_text_tool()`（把文本插到 needle 前/后；needle 必须唯一，否则返回错误）
- `backend/src/tools/cwd_state.py`：`CwdState` 是 bash 与 read_file 共享 cwd 的小状态对象；默认 Agent 每次创建独立 `CwdState`，不能复用全局单例。
- `backend/src/tools/reset_context.py`：`RESET_CONTEXT_TOOL`（工具本身只返回 hint；真正的 reset 编排由 Agent 内部的 reset 流程完成）
补充约束（很容易踩坑）：
- 记忆目录写入守卫在 `backend/src/commons.py`：worker **只能**编辑 `~/.project-x/memories/summaries/TODO.md`；memory manager (summary) 不能编辑 `TODO.md`，应编辑 `MAIN.md` 或其他摘要文件。
- `replace_text/insert_text` 在失败时可能把大段内容落到 `/tmp/...` 并返回 `*_from_file` 路径供下一次调用复用（避免重复粘贴占 token）；这是工具的正常行为。

### 3) 持久化（`backend/src/conversation_store.py`）
- 落地目录：`~/.project-x/memories/originals/`（可用 `PROJECT_X_MEMORIES_ROOT` 覆盖根目录，见 `backend/src/commons.py`）
- `conversation_id`：文件名 `<coolname>-<UTC时间戳>.json`
- JSON 结构：`{ meta: { "memory-manager": {...} }, messages: [ {role, content, ...} ] }`
- 给模型用的 runtime messages 会 strip 掉每条 message 的 `meta`（目前消息本身不带 meta）
补充：memory manager 的运行日志会写到 `~/.project-x/memories/logs/*.jsonl`（见 `backend/src/core/memory_manager_run_logger.py`）。

## 服务层：把 Agent 暴露给前端

### WebSocket 会话编排（`backend/src/websocket_chat_session.py`）
- 每个 WS 连接一个 `WebSocketChatSession`；直接 `await self._agent.run()`，不再做线程桥接
- `ChatEventProjector` 决定 assistant 卡片边界：遇到 tool start 会 close 当前 assistant，因此前端时间线呈 `assistant → tool → assistant`
- 生成状态事件：`agent.became.busy` / `agent.became.idle`（前端用它们驱动 `isGenerating`）
- `conversation.switched`：初始恢复最近会话、reset-context 切 segment、memory manager 的 auto reminder 都走同一事件；payload 只包含 `visibleMessages`，不暴露 conversation 文件名 / `conversationFileName`

### HTTP + WS 路由（`backend/src/web_app.py`、`backend/main.py`）
- `GET /healthz`
- `WS /ws`：收 `send_user_message` / `ping`（命令协议：`backend/src/web_protocol.py`）

## 前端（保持“简单单栏聊天”的约束）

### 核心文件
- 页面布局/交互：`frontend/src/App.tsx`（单栏时间线 + 底部输入；支持“暂停/恢复”；自动滚动与“跳到最新”）
- 协议类型：`frontend/src/features/chat/protocol.ts`（zod 校验；事件含 `conversation.switched` 等）
- Store：`frontend/src/features/chat/store.ts`（核心状态：`items[]`、`pendingUserMessages[]`、`isGenerating`）
- WS 客户端：`frontend/src/features/chat/client.ts`（自动 keepalive `ping`，断线自动重连；用 “active socket” 判定忽略过期 socket 事件以兼容 StrictMode）
- 组件：`frontend/src/features/chat/components/*`（assistant reasoning 默认展开；tool result 过长会折叠）

时间线模型（重要的简化）：
- 前端采用平铺 `user/assistant/tool` items，不表达“assistant 内嵌 tool 段落”；事件顺序决定 UI 顺序
- `zustand@5 + React 19` 注意：selector 不要返回临时新对象/新数组（避免 `Maximum update depth exceeded`）
- ESLint 启用了 `react-hooks/set-state-in-effect`：避免在 `useEffect` 回调体内同步调用 React `setState`，优先把更新放到订阅/异步回调里或改用外部 store

### 测试注意事项
- Playwright strict mode：`getByText()` 容易 strict violation，优先用更具体的 locator（例如 `getByRole('main')...`）
- Codex CLI 沙盒内禁止创建 socket（例如 uvicorn 绑定端口），跑 e2e 需要允许非沙盒执行

## 文档说明
- `docs/draft-plans/`：早期草案/想法，内容不保证与当前实现一致
- `docs/plans/`：实施时写下的更稳定计划文档（通常与当前实现更一致，但仍以代码为准）
- `docs/code_explanations/`：教学/讲义（例如 `teach_backend_asyncio_basics.md`、`teach_frontend_store_basics.md`）
- TODO.md 和 README.md 你不需要去阅读，通常这只会分散你的注意力。
