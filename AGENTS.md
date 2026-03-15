# 项目记忆

- 当前仓库目录名是 `bionic-claw`，项目对外展示名用 `Bionic Claw`。

## 后端
- Python 项目在 `backend/`，使用 `uv` 管理依赖和运行命令。
- `src/core/model_config.py` 里的 `ModelConfig` 提供 `model`、`base_url`、`api_key`，供聊天模型调用使用。
- `src/core/chat.py` 的 `stream()` 负责发起流式模型请求，把 assistant 消息追加回 `messages`，并分别通过回调推送正文增量和思维链增量。
- `stream()` 需要兼容 `tool_calls` 的流式拼装，最终返回 OpenAI 风格的 assistant message dict。
- `stream()` 现在还支持三类工具调用流式事件：开始、参数增量、结束；`Agent` 会把这三类回调继续向外透传。
- 工具系统合并成单一抽象 `src/core/chat.py` 的 `ToolSpec`：同一份定义同时包含给模型看的声明信息和本地 `handler` 实现；`Agent` 构造函数接收 `tools: list[ToolSpec]`，并会拒绝重复的工具名。
- `src/core/chat.py` 的 `execute_tool_and_append()` 会按工具名查找 `ToolSpec`、解析 JSON arguments、调用 `handler`、追加 tool message，并通过最小化的 `OnToolResult(tool_call_id, result_json_str)` 回调把工具执行结果向外透传；当前默认始终返回 `ContinueLoopDirective`。如果模型没给 tool_call_id，会按工具在该 assistant message 里的顺序兜底生成 `tool_call_{index}`。
- `src/core/policies.py` 里有 DeepSeek 特殊规则：发送下一条 user message 前，要去掉上一轮 assistant message 的 `reasoning_content`。
- `src/core/chat.py` 和 `src/core/agent.py` 的回调接口用的是Protocol而不是Callable，因为这样可读性更高。
- `src/tools/bash.py` 提供最基础的 `BASH_TOOL`：入参用 `BashToolInput` 的 pydantic model 校验，只接收 `command`，通过 `bash -lc` 执行并返回 `stdout`、`stderr`、`returncode`。
- `src/core/agent.py` 新增了 `has_pending_user_messages()`，供服务层在一次 run 完成后继续消费排队中的用户消息。
- 后端现在用 `Starlette` 做服务层：`backend/main.py` 暴露 `app` 并通过 `uvicorn.run()` 启动；`src/web_app.py` 提供 `/healthz` 和 `/ws`。
- `src/web_app.py` 的 `ChatSession` 会为每个 WebSocket 连接持有一个 `Agent`，把前端命令解析后入队，并在后台线程里调用 `agent.run()`；AI 正文、思维链、工具调用开始/参数/结束、tool result、user turn 入队/提交，都会被翻译成前端协议事件。
- WebSocket 会话默认使用 `BASH_TOOL`，模型配置通过环境变量 `BIONIC_CLAW_MODEL_CONFIG` 选择，默认值是 `qwen35plus`；连接时如果缺少对应 API key，会直接给前端发 `error` 事件。
- 后端运行时除了 `uvicorn` 还需要安装 WebSocket 协议实现；当前项目依赖里已显式加入 `websockets`，否则访问 `/ws` 时会出现 `Unsupported upgrade request`，并退化成普通 HTTP 404。

## 前端
- 前端在 `frontend/`，当前是 Vite + React 19 + TypeScript。
- 面向当前用户教学的浏览器基础讲义放在 `docs/teach/teach_browser_basics.md`，用于承接 DOM / 布局 / 滚动 / React 与浏览器关系这类更底层的问题。
- 面向当前用户教学的前端基础讲义放在 `docs/teach/teach_frontend_store_basics.md`，后续关于 App / protocol / zustand selector / store 基础问题优先在这份文档对应小节补充，而不是在末尾追加。
- `frontend/src/App.tsx` ：左侧信息侧栏，右侧单栏时间线，底部输入区；侧栏里的新会话、刷新列表、工作目录、暂停等当前后端未提供的功能只做禁用态 mock。
- `frontend/src/App.css` 只保留根节点占满视口，其余样式由 Tailwind 负责。
- `frontend` 已接入 Tailwind CSS v4：`vite.config.ts` 使用 `@tailwindcss/vite` 插件，`src/index.css` 已导入 `tailwindcss`、`tw-animate-css`、`shadcn/tailwind.css` 和 Geist 字体，并定义了 shadcn 主题变量。
- `frontend` 已配置 `@/* -> src/*` 的导入别名，`vite.config.ts`、`tsconfig.json`、`tsconfig.app.json` 都有对应映射。
- `frontend/components.json` 已存在，shadcn 预设为 `base-nova`，别名使用 `@/components`、`@/lib` 等；初始化已生成 `src/lib/utils.ts` 和 `src/components/ui/button.tsx`。
- `frontend/src/features/chat/protocol.ts` 已定义聊天协议的 zod schema 和 TypeScript 类型；用户消息用前端生成的 `userTurnId` 作为稳定主键，不再单独使用 `frontendMsgId` 字段。
- 聊天协议层和前端视图层是两套模型：`userTurnId` 用来把前端本地暂存的待发送用户消息与后端的 `enqueued/committed` 事件对齐；`assistantTurnId` 表示一次 assistant 生成过程，供正文增量、思维链增量、tool call、tool result 归并。`items[]` 只是把这些协议事件投影成时间线卡片，一个 `assistantTurnId` 可能对应多张 item（assistant -> tool -> assistant）。
- `frontend/src/features/chat/store.ts` item 时间线模型：核心状态是 `items[] + pendingUserMessages[]`
- store 会把当前 WebSocket 协议事件投影成时间线 item：首个 reasoning/content delta 才创建 assistant item；`assistant.tool.started` 会截断当前 assistant 段落并创建同一张 tool 卡；tool 之后若 assistant 继续输出，会新建下一个 assistant item，因此视觉顺序稳定为“assistant -> tool -> assistant”。
- tool 卡仍保留 bionic-claw 特有的流式参数能力：`assistant.tool.arguments.delta` 会持续更新同一张 tool 卡里的参数文本，`tool.result` 继续填充同卡结果区域。
- `zustand@5 + React 19` 下，selector 里不要直接返回新数组/新对象这类临时派生值，否则可能触发 `Maximum update depth exceeded`；像 tool card 列表这类派生数据要先取原始 slice，再在组件渲染阶段计算。
- `frontend/src/features/chat/client.ts` 已封装 WebSocket 客户端：负责连接 `/ws`、解析 JSON、用 zod 校验服务端事件、把事件喂给 store，并在发送消息时生成 `userTurnId`。客户端现在会忽略过期 socket 的 `open/message/error/close` 事件，并把主动断开的连接排除出错误路径，避免 React `StrictMode` 下旧连接污染当前连接状态。
- `frontend/src/features/chat/components/` 已拆出聊天 UI 子组件：`user-turn-bubble.tsx`、`assistant-turn-bubble.tsx`、`empty-chat-state.tsx`、`chat-composer.tsx`；`App.tsx` 现在主要负责连接 store/client 和组织页面结构。
- `frontend/vite.config.ts` 已配置本地开发代理：`/healthz` 和 `/ws` 默认转发到 `http://127.0.0.1:8000`，也可通过环境变量 `BIONIC_CLAW_BACKEND_ORIGIN` 覆盖。

## 产品约束
- `docs/spec/brief.md` 明确网页端当前只支持流式交互。
- `docs/spec/frontend.md` 要求前端 UI 保持简约，整体接近 ChatGPT 的单栏聊天页；思维链和工具调用默认展开；同一个工具调用的 tool call 与 tool result 要放在同一张卡片里；前端技术栈为 `ts + zustand store + tailwind + shadcn + zod`。
