# WebSocket 集成测试（真实模型 + bash 工具调用）

## 目标
做一个尽可能贴近真实运行形态的集成测试，覆盖：
1) 多轮对话（“hi, 我叫小明” → “你知道我叫什么名字吗？”）
2) 模型触发 `bash` 工具调用（“请调用bash工具查看当前时间。”）
3) 通过 `backend/src/web_app.py` 的 `/ws` 端点贯穿整条链路（协议解析 → session → agent → tool → 事件投影）。

## 为什么不用 mock（已确认）
本测试明确选择真实模型，接受其不确定性与外部依赖（API/网络/配额/偶发超时）。

## 关键风险与兜底
- **非确定性**：真实模型不保证一定产生 tool_call。
  - 兜底：测试里把模型温度尽量设为 0（如当前实现允许），并把第三句写成强约束指令（例如明确要求必须调用 `bash`）。
- **外部依赖缺失**：未配置 API key / OAuth 凭据时应跳过。
  - 兜底：pytest 检测环境变量/凭据不可用就 `pytest.skip(...)`，避免把“环境没配好”误报成产品 bug。
- **端口/网络限制**：需要真实监听端口并通过 ws 连接。
  - 兜底：端口随机选择；CI/沙盒不允许时用 `-m integration` 默认跳过。

## 实现思路（自顶向下）
抽象：`RealWsClient`（测试内使用）驱动“真实服务”并读取事件流。

```
pytest
  ├─ 启动 uvicorn(Starlette app=build_app())
  ├─ websockets.connect("ws://127.0.0.1:{port}/ws")
  ├─ send_user_message(x3)
  └─ 读取 server events，断言：
       - 第二轮 assistant 内容包含 "小明"
       - 第三轮出现 tool.started/tool.completed/tool.result 且 toolName == "bash"
```

### 1) 服务启动方式
- 直接 import `backend/src/web_app.py:build_app()` 得到 Starlette app。
- 使用 `uvicorn.Server` 在后台线程启动监听（真实 socket，符合“思路C”）。
- fixture 负责：找空闲端口 → 启动 → 等待 `/healthz` ready → 测试结束后优雅退出。

### 2) WebSocket 客户端与断言
- 通过 `websockets` 连接 `/ws`。
- 客户端命令使用 `backend/src/web_protocol.py` 的约定：
  - `{"type":"send_user_message","userMessageId": "...","content":"..."}`
- 事件读取：
  - 组装 `assistant.message.delta(channel="content")` 成最终文本（遇到 `assistant.message.completed` 收束一次回答）。
  - 记录 `tool.started`/`tool.completed`/`tool.result`，确认 `bash` 相关事件存在且顺序合理。

### 3) 测试放置与运行策略
- 新增测试文件：`backend/tests/integration/test_ws_real_model.py`
- 加 `@pytest.mark.integration`，默认 `uv run pytest -q` 不跑，避免污染单测。

## 测试思路（如何跑）
1) 配好真实模型：
   - deepseek/qwen：设置对应 API key；或
   - openai-codex：本机已用 Codex CLI 登录（满足 `resolve_codex_tokens(...)`）。
2) 运行：
   - `cd backend`
   - `uv run pytest -q -m integration --tb=line`

## 假设
- 运行环境允许打开本地端口并进行 ws 连接。
- 真实模型可用且配额充足；若不可用则测试应 skip 而不是 fail。

