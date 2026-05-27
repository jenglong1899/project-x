# 计划：project-x 接入 OpenAI Codex（OAuth 订阅路径）

## 背景与目标

- 目标：让 project-x 像 hermes-agent 的 `openai-codex` 一样，复用 ChatGPT/Codex 订阅能力（OAuth token），通过 `chatgpt.com/backend-api/codex` 进行推理，并保留 project-x 现有的：
  - 流式 assistant delta
  - tool call 流式参数拼接与本地执行
  - 现有 WebSocket 事件协议与持久化
- 非目标：不试图把 Codex backend 伪装成 OpenAI-compatible 的 ChatCompletions；不继续走 `litellm` 这条链路（仅保留给 deepseek/qwen/mock）。

## 雷点与约束（先确认我们接受）

- 这是非公开接口路径：接口形态可能变化，需准备“可快速替换”的隔离层（`codex_client.py`）。
- Auth 刷新/写盘存在并发竞态：需要跨进程文件锁（参考 hermes 的 auth store lock 思路）。
- 只实现最小闭环：先跑通“单轮对话 + tool call + 多轮续写”，再补模型列表、选择器等产品化能力。

## 总体实现思路（自顶向下）

新增一个“推理后端抽象”，让现有 `agent_turn.stream()` 变成两条路径：

```
                 +----------------------+
messages/tools ->| InferenceBackend     |-> assistant msg dict
                 |  - stream()          |   (content/tool_calls/...)
                 |  - execute_tools()   |   (复用现有逻辑)
                 +----------+-----------+
                            |
         +------------------+------------------+
         |                                     |
  LiteLLMBackend（现有）                CodexBackend（新增）
  - deepseek/qwen/mock                  - openai-codex (OAuth)
  - acompletion(stream)                 - httpx stream + 自己做 delta 适配
```

CodexBackend 内部再拆两层：

```
CodexBackend
  |
  +-- CodexAuth（令牌来源/刷新/导入）
  |
  +-- CodexClient（HTTP 协议、流式解析、错误映射）
```

## 关键模块设计

### 1) CodexAuth：完全照 hermes 的做法

目标：优先使用 project-x 自己的 token 存储，避免与 Codex CLI/VSCode 的 refresh 互相挤下线；但允许“只读导入” `~/.codex/auth.json` 作为迁移/首启体验。

- 存储：`~/.project-x/auth.json`（或由现有 `PROJECT_X_MEMORIES_ROOT` 推导一个同级根目录，保持可配置）
- 读取顺序：
  1. project-x 自己的 auth store（主路径）
  2. `~/.codex/auth.json`（仅导入；导入后写入 project-x auth store）
- 刷新策略：
  - 每次推理前：若 access token 接近过期则 refresh
  - refresh 必须在文件锁内做“读-判定-刷新-写”以避免并发竞争
- 登录方式：
  - 先实现 device code 登录（可选命令行入口），流程与 hermes 的 `_codex_device_code_login()` 保持一致

### 2) CodexClient：协议隔离层

目标：把 project-x 的 `messages/tools` 转为 Codex backend 的请求格式，并把返回流解析为 project-x 现有事件（content_delta / tool_call_delta / tool_call_finished）。

- 输入：`messages: list[dict]`、`tools: list[Tool]`
- 输出：与现有 `agent_turn.stream()` 返回一致的 `assistant_message` dict（含 `tool_calls` 结构）
- 解析：必须支持
  - content delta
  - tool call name/arguments 的增量拼接（按 index 聚合）
  - 错误：401/403 触发“强制 refresh/重新登录”提示；其他错误映射成可读错误信息
- 适配策略：
  - 先用“兼容层”把 Codex 的流式事件翻译成“仿 OpenAI tool_calls delta”的形态，尽量复用现有 `_merge_tool_call_delta()` 等逻辑

### 3) ModelConfig/路由：引入 provider 概念

现状 `ModelConfig` 只有 (model, base_url, api_key)，且所有非 mock 都走 `litellm.acompletion`。

改造目标：不把 Codex OAuth 硬塞进 `api_key/api_base`，而是显式声明 `provider`：

- `provider="litellm"`：沿用现有 deepseek/qwen/mock
- `provider="openai-codex"`：走 CodexBackend（由 CodexAuth 提供 token）

环境变量层面（对齐 project-x 风格）：
- `PROJECT_X_MODEL_CONFIG=openai-codex`（新增一个选项）
- 可选：`PROJECT_X_CODEX_BASE_URL`（默认 `https://chatgpt.com/backend-api/codex`）
- 可选：`PROJECT_X_CODEX_HOME`（默认 `~/.codex`，仅用于导入）

## 测试思路（只细分一层）

### 单元测试

- `CodexAuth`
  - 能从 project-x auth store 读到 tokens
  - 能从 `~/.codex/auth.json` 导入（用临时目录 + monkeypatch `Path.home()` 或 env）
  - refresh 逻辑：模拟“将过期”触发 refresh（用 httpx mock / respx）
  - 并发：两个协程/进程级锁的基本覆盖（至少验证锁文件存在与串行写入）
- `CodexClient`
  - 请求构造：messages/tools 变换后的 payload 快照（不依赖真实网络）
  - 流式解析：喂入构造的 event 流，断言 content/tool_calls 聚合正确
  - 错误映射：401/403 与普通 5xx 的提示差异

### 集成测试（最小闭环）

- 在 `backend/tests/` 加一个“CodexBackend mocked”测试：
  - 模拟一次 tool call：assistant → tool_call → tool result → assistant
  - 断言 WebSocket 事件投影顺序不变（assistant 卡片边界仍然正确）

## 交付拆分（便于每次 ≤100 行改动）

1. 引入 `InferenceBackend` 抽象 + 把现有 litellm 路径封装为 `LiteLLMBackend`
2. 新增 `CodexAuth`（先只做“读 project-x store + 导入 ~/.codex/auth.json”，不做 login）
3. 新增 `CodexClient`（先用 mock stream 测试跑通解析/聚合）
4. `CodexBackend` 串起来接入 `AgentTurn`（先跑通无工具的纯文本）
5. 打通 tool call：Codex → tool_calls → 本地执行 → 续写
6. 补 `project-x auth` 或等价入口（若需要交互式登录）

