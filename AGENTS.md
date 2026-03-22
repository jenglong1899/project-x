# 项目记忆

## 开发脚本
- 根目录 `dev.sh`：一键启动前端/后端（`npm run dev` + `PYTHONPATH=. uv run python main.py`），Ctrl+C 会同时停止两个进程。

## 前端

### 技术栈与基础设施
- 前端目录是 `frontend/`，技术栈是 Vite + React 19 + TypeScript。
- `frontend` 已接入 Tailwind CSS v4：`vite.config.ts` 使用 `@tailwindcss/vite` 插件，`src/index.css` 已导入 `tailwindcss`、`tw-animate-css`、`shadcn/tailwind.css` 和 Geist 字体，并定义了 shadcn 主题变量。
- `frontend` 已配置 `@/* -> src/*` 导入别名，`vite.config.ts`、`tsconfig.json`、`tsconfig.app.json` 都有对应映射。
- `frontend/components.json` 已存在，shadcn 预设为 `base-nova`，别名使用 `@/components`、`@/lib` 等；初始化已生成 `src/lib/utils.ts` 和 `src/components/ui/button.tsx`。
- `frontend/vite.config.ts` 已配置本地开发代理：`/healthz`、`/conversations`、`/ws` 默认转发到 `http://127.0.0.1:8000`，也可通过环境变量 `BIONIC_CLAW_BACKEND_ORIGIN` 覆盖。

### 页面结构
- `frontend/src/App.tsx` 采用左侧会话侧栏、右侧单栏时间线、底部输入区的布局；侧栏当前只保留“新建对话 + 最近会话”，会话列表只在页面初始化时通过 HTTP 拉取一次，点击某个会话即切换，并通过断开重连 WebSocket（`/ws?conversationId=...`）实现 resume。
- `frontend/src/App.css` 只保留根节点占满视口，其余样式由 Tailwind 负责。
- `frontend/src/features/chat/components/` 已拆出聊天 UI 子组件：`user-turn-bubble.tsx`、`assistant-turn-bubble.tsx`、`chat-composer.tsx`、`chat-sidebar.tsx`、`tool-call-card.tsx`；当前并没有 `empty-chat-state.tsx`，空态文案直接在 `App.tsx` 中渲染。

### 聊天协议与数据模型
- `frontend/src/features/chat/protocol.ts` 已定义聊天协议的 zod schema 和 TypeScript 类型；用户消息主键是前端生成的 `userMessageId`，assistant 消息主键是后端生成的 `messageId`，工具卡主键是 `toolCallId`。
- `frontend/src/features/chat/store.ts` 的核心状态是 `items[] + pendingUserMessages[] + isGenerating + activeConversationId`，不再维护 `assistantTurnId`、active assistant segment map、tool item map 这类中间索引。
- 当前前端时间线是平铺模型，只存在 `assistant item` 和 `tool item`，表达不了“一个 assistant message 内部嵌 tool 段落”的结构。这种UI设计不会（或者说基本不会）降低用户的体验，还能降低代码复杂度。
- assistant 卡片边界由后端决定，前端只按事件顺序投影时间线：`assistant.message.started/delta/completed` 直接驱动 assistant item，`tool.started/arguments.delta/completed/result` 直接驱动 tool item，因此时间线稳定呈现为 `assistant -> tool -> assistant`，但不需要前端自己截断段落。
- 之所以采用平铺的 `assistant -> tool -> assistant` 时间线，是因为前后端都更简单：后端协议保持扁平，前端 store 只维护平铺 `items[]`，渲染和测试也只需要按事件顺序处理；若改成“tool block 嵌在 assistant message block 内”，语义更高保真，但协议、store、渲染和测试都会更复杂。
- tool 卡保留流式参数能力：`tool.arguments.delta` 传的是真正的 `argumentsDelta` 增量，前端在 store 里按顺序追加；`tool.completed` 继续下发最终完整 `arguments` 作为收尾校准，`tool.result` 继续填充同卡结果区域。另外一种方案是，后端发送最新版本的argument，前端不需要管拼接，直接替换掉旧的结果就行，但这样页面大了，容易导致渲染卡顿。
- `zustand@5 + React 19` 下，selector 里不要直接返回新数组或新对象这类临时派生值，否则可能触发 `Maximum update depth exceeded`；像 tool card 列表这类派生数据要先取原始 slice，再在组件渲染阶段计算。
- `frontend/src/features/chat/client.ts` 已封装 WebSocket 客户端：负责连接 `/ws`（切换会话时走 `/ws?conversationId=...`）、解析 JSON、用 zod 校验服务端事件、把事件喂给 store，并在发送消息时生成 `userMessageId`、先写入 `pendingUserMessages`，再通过 WebSocket 发送命令。客户端会忽略过期 socket 的 `open/message/error/close` 事件，并把主动断开的连接排除出错误路径，以避免 React `StrictMode` 下旧连接污染当前连接状态。
- `frontend/src/features/chat/conversations.ts` 负责会话 HTTP API（list/detail）以及将后端历史 messages 一次性转换成前端 `ChatItem[]`（卡片 id 由前端运行时随机生成；tool 卡按 `tool_call_id` 回填 result）。
- 会话切换约束：当 `pendingUserMessages` 非空或 `isGenerating=true` 时，前端禁用切换会话/新会话按钮，避免竞态。

### 测试
- Playwright 默认 strict mode；断言 `getByText()` 时若侧栏/主区存在重复文本，容易触发 strict mode violation，优先用 `getByRole('main')...` 或更具体的 role/locator 来缩小范围。
- Codex CLI 沙盒内禁止创建 socket（例如 uvicorn 绑定端口），运行 Playwright e2e 需要允许非沙盒执行。

## 产品约束
- `docs/zh/spec/brief.md` 明确网页端当前只支持流式交互。
- `docs/zh/spec/frontend.md` 要求前端 UI 保持简约，整体接近 ChatGPT 的单栏聊天页；思维链和工具调用默认展开；同一个工具调用的 tool call 与 tool result 必须放在同一张卡片里；前端技术栈固定为 `ts + zustand store + tailwind + shadcn + zod`。

## 后端

### 基础约束
- Python 项目位于 `backend/`，使用 `uv` 管理依赖和运行命令。
- `backend/pyproject.toml` 已配置 pytest 的 `pythonpath = ["."]`，这样从 `backend/` 运行测试时，`from src...` 导入可以直接工作。
- `backend` 已通过 `[dependency-groups].dev` 声明 `pytest`，可以直接在 `backend/` 下运行 `uv run pytest -q`。
- 仓库从 `catclaw` 重命名到 `bionic-claw` 后，PyCharm 终端可能暂时保留旧的 `VIRTUAL_ENV=/home/bruce/projects/catclaw/backend/.venv`；`uv` 会忽略它并使用当前项目的 `.venv`，关闭终端标签或重启 PyCharm 后通常恢复正常。

### 模型流式调用与工具系统
- `src/core/model_config.py` 里的 `ModelConfig` 提供 `model`、`base_url`、`api_key`，供聊天模型调用使用。
- `src/core/agent_turn.py` 的 `stream()` 负责发起流式模型请求，返回最终 assistant message dict，并分别通过回调推送正文增量和思维链增量；它本身不再往 `messages` 里 append。
- `stream()` 需要兼容 `tool_calls` 的流式拼装，最终返回 OpenAI 风格的 assistant message dict。
- `stream()` 还支持三类工具调用流式事件：开始、参数增量、结束；`Agent` 会把这三类回调继续向外透传。
- 工具系统合并成 `src/core/agent_turn.py` 里的单一抽象 `ToolSpec`：同一份定义同时包含给模型看的声明信息和本地 `handler` 实现；`Agent` 构造函数接收 `tools: list[ToolSpec]`，并会拒绝重复工具名。
- `src/core/agent_turn.py` 的 `execute_tool_calls()` 会按工具名查找 `ToolSpec`、解析 JSON arguments、调用 `handler`，并通过最小化的 `OnToolResult(tool_call_id, result_json_str)` 回调把工具执行结果向外透传；它返回待 append 的 `tool_messages`，但不直接改 `messages`。
- `src/core/policies.py` 里有 DeepSeek 特殊规则：发送下一条 user message 前，要去掉上一轮 assistant message 的 `reasoning_content`。
- `src/core/agent_turn.py` 和 `src/core/agent.py` 的回调接口都用 Protocol，而不是 Callable，以提升可读性。
- `src/tools/bash.py` 提供最基础的 `BASH_TOOL`：入参用 `BashToolInput` 的 pydantic model 校验，只接收 `command`，通过 `bash -lc` 执行并返回 `stdout`、`stderr`、`returncode`。

### Prompt 与记忆
- `src/prompts/builder.py` 里的记忆路径常量保留 `~` 形式，给 system instruction 复用；真正读写文件时再 `expanduser()`。
- `build_user_level_instruction_zh()` 会确保 `~/.bionic-claw/memories/summary/main.md` 存在；首次缺失时自动创建，并写入默认记忆“用户刚完成bionic-claw的安装，还没让我做什么事情”。
- 当前主记忆是 `build_user_level_instruction_zh()` 在 agent 构造时一次性读入 `user_instruction` 的快照；运行中的 agent 不会因为 `main.md` 被异步改写而自动看到最新内容，除非后续显式重建 instruction 或 reset context。
- `src/conversation_store.py` 会把原始对话以 JSON 文件存到 `~/.bionic-claw/memories/originals/`；文件名格式是 `coolname + UTC时间戳 + .json`，根结构固定为 `meta + messages`，并用 `has_persisted_conversation()` 表示“这轮会话已经有落地 JSON”。
- conversation_id 就是上述 JSON 文件名；`ConversationStore.load_from_conversation_id()` 用 conversation_id 直接加载并恢复存储消息；`ConversationStore.build_runtime_messages()` 会去掉每条消息的 `meta`，避免把存储字段传给模型供应商。
- conversation JSON 只会在“首条 committed 的后续 user message 进入 `_messages`”时创建，不会在 `new_conversation()` 或 `enqueue_user_message()` 时创建空会话文件。
- conversation JSON 的 `meta.display-name` 取首条 committed 的后续 user message，最多保留 10 个字符；`messages` 中每条消息都会额外带一个 `meta.timestamp`。

### 服务层与会话编排
- 后端当前使用 Starlette 做服务层：`backend/main.py` 暴露 `app` 并通过 `uvicorn.run()` 启动；`src/web_app.py` 只保留 `/healthz`、`/ws` 路由和 WebSocket 收发。
- `src/web_app.py` 新增 HTTP API：`GET /conversations`（会话列表，按 `lastChatTime` 倒序）和 `GET /conversations/{conversationId}`（会话详情 messages）。
- `src/websocket_chat_session.py` 负责每个 WebSocket 连接的会话编排：持有 `Agent`、维护待提交 user message 内容、把同步 `agent.run()` 通过 `asyncio.to_thread()` 桥接回异步事件循环。
- WebSocket 支持 query：`/ws?conversationId=...`，用于 resume 某个历史会话；新会话首次持久化后会发送 `conversation.persisted` 事件（包含 `conversationId`、`displayName`）。
- `src/core/agent.py` 提供 `has_pending_user_messages()`，供服务层在一次 run 完成后继续消费排队中的用户消息。
- `src/websocket_chat_session.py` 里的 `ChatEventProjector` 会把 `Agent` 的低层回调投影成前端协议事件，并由后端决定 assistant 卡片边界：首次 reasoning/content delta 时开启消息，遇到 tool start 时结束当前 assistant 消息，每次 `agent.run()` 返回时也会结束当前 assistant 消息。由于前端采用平铺 `items[]` 模型，如果同一个 AI message 的流式顺序是先输出一段 content、再输出 tool call、然后继续输出 content，前端时间线会被拆成 `assistant -> tool -> assistant`。
- WebSocket 协议不再暴露 `assistantTurnId`，也不再单独发送 `session.started`；当前服务端事件以 `messageId`、`toolCallId`、`userMessageId` 为主键，包含 `generation.started/completed`、`user.message.committed`、`assistant.message.started/delta/completed`、`tool.started/arguments.delta/completed/result`。其中 `tool.arguments.delta` 的 payload 是 `argumentsDelta`，`tool.completed` 的 payload 仍是完整 `arguments`。
- WebSocket 会话默认使用 `BASH_TOOL`，模型配置通过环境变量 `BIONIC_CLAW_MODEL_CONFIG` 选择，默认值是 `qwen35plus`；连接时如果缺少对应 API key，会直接给前端发 `error` 事件。
- 后端运行时除了 `uvicorn` 还需要安装 WebSocket 协议实现；当前项目依赖里已显式加入 `websockets`，否则访问 `/ws` 时会出现 `Unsupported upgrade request`，并退化成普通 HTTP 404。
- `backend/tests/test_websocket_chat_session.py` 用假 `Agent` 覆盖了两类关键时序：`assistant -> tool -> assistant` 的流式事件顺序，以及同一生成期内连续消费多条排队 user message。

# 文档
## docs/zh/spec
这里面的文档描述的功能有些实现了，有些还没实现。

## docs/zh/teach/

- 浏览器基础讲义位于 `teach_browser_basics.md`，用于承接 DOM、布局、滚动、React 与浏览器关系等更底层的问题。
- 前端基础讲义位于 `teach_frontend_store_basics.md`；后续关于 App、protocol、zustand selector、store 基础问题，优先在这份文档对应小节补充，而不是在文末追加。
- 后端异步基础讲义位于 `teach_backend_asyncio_basics.md`；后续关于 `asyncio`、事件循环、`await`、`create_task()`、`to_thread()`、`AbstractEventLoop`、WebSocket 会话并发模型的问题，优先在这份文档对应小节补充。
