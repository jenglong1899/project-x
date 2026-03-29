# Project X 代码库改进建议（2026-03-28）

> 目标：只写“我能在当前仓库里直接验证/定位”的问题与改进点；尽量避免猜测，降低误判风险。

## 审查范围与验证方式

- 范围：`backend/src/*`、`backend/tests/*`、`frontend/src/*`（含聊天协议/状态/核心 UI）。
- 方式：静态阅读 + 本地命令验证（不启动服务、不做真实外部 API 调用）。
- 已验证命令结果：
  - 后端单测：`cd backend && PYTHONPATH=. uv run --with pytest python -m pytest -q` → **22 passed**。
  - 前端 ESLint：`cd frontend && npm run lint` → **当前不通过（5 个 error）**，详见本文“前端：Lint 未通过”。

## 高确定性（直接可复现/可定位）

### 1) WebSocket 命令解析对“非 object JSON”不够健壮

- 位置：`backend/src/web_protocol.py` 的 `parse_client_command(payload: dict[str, Any])`
- 现状：`websocket_endpoint()` 中 `payload = json.loads(payload_text)` 后直接调用 `parse_client_command(payload)`（见 `backend/src/web_app.py`）。
  - 当客户端发送的 JSON 顶层不是 object（例如 `[]` / `"x"` / `123`），`parse_client_command` 会触发 `AttributeError: '<type>' object has no attribute 'get'`。
  - `websocket_endpoint()` 只捕获 `(ValidationError, ValueError)`，因此这类异常会绕过错误回包逻辑，影响连接稳定性。
- 建议：
  - 让 `parse_client_command` 接受 `payload: Any` 并在内部做 `isinstance(payload, dict)` 校验，统一转成 `ValueError`（从而走现有 `invalid_command` 分支回包）。
  - 或者在 `websocket_endpoint()` 中，在调用 `parse_client_command` 前做一次顶层类型检查。

### 2) 工具调用强依赖 `tool_call_id` 非空，兼容性边界很硬

- 位置：
  - `backend/src/websocket_chat_session.py`：`ChatEventProjector._require_tool_call_id()` 在 `tool_call_id` 为空时直接 `raise RuntimeError(...)`
  - `backend/src/core/agent_turn.py`：`execute_tool_calls()` 生成 tool message 时会写入 `tool_call_id`（可能为 `None`），前端协议 `frontend/src/features/chat/protocol.ts` 又要求 `toolCallId` 为非空字符串
- 风险：一旦某个供应商/中间层返回的 `tool_call_id` 缺失或为空字符串，当前链路会直接报错并中断，而不是降级继续。
- 建议（按侵入性从低到高）：
  - 明确在“模型/供应商适配层”统一保证 `tool_call_id`（例如基于 `(conversation_id, index)` 生成稳定 fallback id），并让 tool message / 事件使用同一套规则。
  - 或者把前端协议放宽（允许空/缺失），但这会影响 UI 关联 tool result 的逻辑，需要同步设计。

### 3) 工具参数解析/工具执行异常会把整轮 run 打断（缺少“回传给模型自行纠错”的兜底）

- 位置：`backend/src/core/agent_turn.py` 的 `execute_tool_calls()`
- 现状：
  - `_parse_tool_arguments()` 里 `json.loads(arguments)` 失败会直接抛异常。
  - `tool_spec.handler(...)` 抛异常也会直接上抛。
  - 最终表现：`Agent.run()` / `WebSocketChatSession._run_agent_until_idle()` 进入异常分支，前端收到 `error: agent_run_failed`（见 `backend/src/websocket_chat_session.py`）。
- 建议：
  - 在 `execute_tool_calls()` 内部捕获：
    - `json.JSONDecodeError` / `ValueError`（参数解析）
    - `Exception`（handler 执行）
  - 将异常包装成结构化 tool result（例如 `{"error": "...", "tool": "...", "stage": "parse|run"}`），再继续循环，让模型有机会在下一条 assistant message 中自我修正（类似当前对 `reset_context` 并发的处理方式）。

### 4) `bash` 工具缺少超时/输出上限/资源约束，容易卡死或拉爆输出

- 位置：`backend/src/tools/bash.py` 的 `subprocess.run(["bash","-lc", ...], capture_output=True, ...)`
- 现状：无 `timeout`，无输出截断，允许执行任意命令。
- 影响：
  - 一条 `sleep 999999` 或持续输出的命令会让本轮 `Agent.run()` 长时间挂起。
  - 大输出会在内存与持久化层（conversation JSON）放大成本。
- 建议：
  - 增加 `timeout`（并将超时转成可读 tool result，而不是抛异常）。
  - 对 `stdout/stderr` 做长度上限截断（保留 head/tail + 原始长度）。
  - （可选）增加 allowlist / 工作目录隔离 / 环境变量隔离（如果未来要对外部署）。

### 5) `Agent.run()` 对“无 user message 就开始生成”的前置条件缺少显式校验

- 位置：`backend/src/core/agent.py` 的 `run()`
- 现状：`new_conversation()` 后如果外部误调用 `run()`（队列为空），会进入模型生成路径；随后 `_append_runtime_message()` 会调用 `ConversationStore.append_message()`，而会话文件尚未创建，会抛 `RuntimeError("conversation 尚未开始...")`。
- 建议：
  - 在 `run()` 开头增加显式校验：若本轮 drain 后仍未持久化会话且无排队消息，则直接 `raise RuntimeError("没有 user message，不能 run")` 或直接返回（取决于你希望的 API 语义）。
  - 这能把错误从“深处的持久化异常”变成“清晰的调用约束错误”。

### 6) 前端：Lint 未通过（当前 `npm run lint` 会失败）

- 位置与报错（来自 `npm run lint` 输出）：
  - `frontend/src/App.tsx`：`react-hooks/set-state-in-effect`
    - `useEffect(() => { void loadSessionList() }, [loadSessionList])`
    - `useEffect(() => { setSessionEntries(...) }, [persistedConversation])`
  - `frontend/src/features/chat/conversations.ts`：`@typescript-eslint/no-explicit-any`（3 处 `as any`）
- 建议：
  - 若这些规则是你想强制执行的：改写逻辑/类型消除报错（例如为 tool call 结构定义更明确的类型守卫，而不是 `as any`）。
  - 若这些规则不适合当前写法：在 `eslint.config.js` 里对特定文件/目录降级或关闭对应规则，并解释原因（避免“默认脚本永远红”）。

## 中确定性（需要产品/规模假设才能定优先级）

### 7) 会话列表的可扩展性：`/conversations` 需要全量读 JSON

- 位置：`backend/src/web_app.py` 的 `list_conversations()`
- 现状：遍历 `originals/*.json` 并 `json.loads` 全文件，再从 messages 中反向找 `meta.timestamp`。
- 可能问题：会话数/单会话消息数大时，列表接口会变慢（I/O + JSON 解析）。
- 可选方向：
  - 维护一个轻量索引（例如 `index.json` / sqlite），写入时增量更新（与 `ConversationStore._write_json_atomically()` 同步）。
  - 或把 `lastChatTime`/`displayName` 冗余到文件头部并只读取前 N KB（但这要求文件格式设计支持）。

### 8) `truncate_display_name` 的语义可以更明确

- 位置：`backend/src/conversation_store.py` 的 `truncate_display_name()`，以及 `backend/tests/test_conversation_store.py`
- 现状：实现/测试语义是“保留前 20 个字符，再追加 `...`”，因此最终长度可能是 23（测试也这样断言）。
- 建议：将常量/命名从“最终长度”表述改成“保留前缀长度”表述，或在文档里明确规则，避免误解。

### 9) 线程桥接的长期方向：把 `Agent.run()` 全链路异步化

- 位置：`backend/src/websocket_chat_session.py` 的 TODO
- 现状：用 `asyncio.to_thread(self._agent.run)` 桥接同步 run；逻辑正确但线程与回调跨线程会增加复杂度。
- 建议：如果未来工具/模型调用都以 I/O 为主，可以考虑逐步把回合引擎改为 async（先从模型流式与工具执行开始）。

## 建议的改进落地顺序（偏“收益/风险比”）

1. 修复 WebSocket 命令解析对非 object JSON 的健壮性（低风险、高收益）。
2. 给 `bash` 工具加 `timeout` 与输出截断（避免卡死与超大落盘）。
3. `execute_tool_calls()` 增加 parse/handler 异常兜底并回传 tool result（提升系统自恢复能力）。
4. 处理前端 lint 红（要么改代码要么改规则，避免 CI/开发体验长期受损）。
5. 根据会话规模决定是否做会话索引与性能优化。

