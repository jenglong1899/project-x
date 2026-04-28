# 跨进程工具调用计划

codex resume 019dd38d-9792-7d53-887d-f0cbcf2e50d7

## 背景和目标

用户的描述：
```
在agent开发中，有个问题就是你不能随意的在中途添加或修改或删除某个工具, 否则会破坏缓存。

所以我打算以 skill 的形式做这种工具。但传统的 skill 工具没法和 agent 系统交互，因为这是两个进程。

Agent可以用我提前写好的py脚本，然后当它通过shell工具执行这个py文件的时候，这个文件会向我的 Agent 系统发送 HTTP 请求，这个 HTTP 请求里面就带着工具的名字，还有参数。然后我的系统就会执行这个工具，把结果以http的形式返回给py文件。
```

```text
Agent
  |
  | bash 执行用户预先写好的脚本
  v
用户维护的 Python 脚本
  |
  | 脚本内部处理 HTTP 请求、密钥、参数文件、错误格式
  v
project-x 本地 HTTP endpoint
  |
  v
InterprocessToolRegistry 显式注册的后端工具 handler
```


## 非目标

- 第一版不让 Agent 生成或改写这些 Python 脚本；脚本由用户提前维护。
- 第一版不提供远程公开 API；HTTP endpoint 只面向本机脚本调用。
- 第一版不做插件市场、MCP、外部服务编排；但抽象命名和结果模型不堵死以后扩展。
- 不把现有所有 Agent 工具自动暴露给跨进程入口；只暴露显式注册到 `InterprocessToolRegistry` 的工具。

## 核心抽象

### `InterprocessTool`

`InterprocessTool` 表示“可以被用户预置脚本通过本地 HTTP 调用的工具”。

字段：

- `name: str`：跨进程工具名，作为 HTTP 请求里的工具标识。
- `description: str`：给脚本文档或调试信息使用，不直接作为模型原生 tool schema。
- `input_schema: dict[str, Any]`：参数 JSON schema，用于接口校验失败时返回清晰错误。
- `handler: InterprocessToolHandler`：异步 handler，签名为 `async def (*, arguments: dict[str, Any]) -> Any`。

第一版可以复用现有 `ToolSpec` 的 handler 约定，但不要直接把模型原生 `ToolSpec` 当成跨进程工具注册表。原因是二者的暴露对象不同：

- `ToolSpec` 面向模型原生 tool calling。
- `InterprocessTool` 面向本地脚本和 HTTP endpoint。

如果某个 handler 同时适合两边使用，可以共享 handler 函数和 pydantic 输入模型，但注册动作必须是显式的。

### `InterprocessToolRegistry`

`InterprocessToolRegistry` 负责保存显式允许跨进程调用的工具。

职责：

- 注册 `InterprocessTool`。
- 按 `tool_name` 查找工具。
- 启动时拒绝重复工具名。
- 提供只读的工具列表，便于后续做调试 endpoint 或脚本文档生成。

它不负责：

- 解析 HTTP 请求。
- 做鉴权。
- 执行 handler。
- 格式化 HTTP 响应。

### `InterprocessToolDispatcher`

`InterprocessToolDispatcher` 负责执行一次跨进程工具调用。

输入模型：

```text
InterprocessToolRequest
  tool_name: str
  arguments: dict[str, Any]
```

输出模型：

```text
InterprocessToolResponse
  ok: bool
  tool_name: str
  result?: Any
  error?: {
    code: str
    message: str
    stage: "auth" | "lookup" | "validate" | "run"
  }
```

Dispatcher 的行为：

1. 从 registry 查找 `tool_name`。
2. 找不到时返回 `ok=false`，`stage="lookup"`。
3. 用工具自己的输入模型或 schema 校验 `arguments`。
4. 调用 handler。
5. handler 成功时返回 `ok=true` 和 `result`。
6. handler 抛异常时记录后端日志，并返回 `ok=false`，`stage="run"`。

错误响应应当适合脚本原样打印给 Agent，因此 message 要清楚，但不能泄露密钥、完整环境变量或不必要的本机路径。

## HTTP 接口

第一版新增一个本地接口：

```text
POST /interprocess-tools/call
Authorization: Bearer <token>
Content-Type: application/json

{
  "toolName": "some_tool",
  "arguments": {
    "...": "..."
  }
}
```

响应：

```text
{
  "ok": true,
  "toolName": "some_tool",
  "result": {}
}
```

失败响应仍然返回 JSON：

```text
{
  "ok": false,
  "toolName": "some_tool",
  "error": {
    "code": "tool_not_found",
    "message": "未注册的跨进程工具: some_tool",
    "stage": "lookup"
  }
}
```

HTTP status 约定：

- `200`：请求格式合法，工具执行成功或业务错误已被包装进 `ok=false`。
- `400`：请求 JSON 结构不合法，或 `toolName` / `arguments` 类型错误。
- `401`：缺少 token 或 token 不正确。
- `500`：dispatcher 外层出现未预期系统错误。正常 handler 异常不走这个分支，而是返回 `ok=false`。

## 鉴权与配置

后端通过环境变量读取 token：

```text
PROJECT_X_INTERPROCESS_TOOL_TOKEN
```

规则：

- 未配置 token 时，`/interprocess-tools/call` 不可用，返回明确错误。
- 请求必须使用 `Authorization: Bearer <token>`。
- 后端日志不能打印 token。
- 用户维护的 Python 脚本负责读取或内置 token；Agent 不需要知道 token 内容。

第一版不做多 token、权限分组、过期时间和审计日志。以后如果跨进程工具变多，再引入这些机制。

## 参数传递约定

HTTP endpoint 只接受标准 JSON object。不要引入“类似 JSON”的格式。

为了避免 shell 转义问题，用户预置脚本应支持参数文件，例如：

```text
some-tool --args-file /path/to/args.json
```

脚本内部读取 JSON 文件后，再向 `/interprocess-tools/call` 发送：

```json
{
  "toolName": "some_tool",
  "arguments": {}
}
```

这意味着 Agent 的职责是：

1. 在需要复杂参数时，把标准 JSON 写入文件。
2. 执行用户预置脚本并传入 `--args-file`。
3. 根据脚本输出继续工作。

Agent 不负责：

- 拼 HTTP URL。
- 设置 HTTP header。
- 处理 token。
- 手写复杂 JSON 转义。

## 文件和模块布局

建议新增：

```text
backend/src/interprocess_tools/
  __init__.py
  models.py        # request/response/error 数据模型
  registry.py      # InterprocessTool / InterprocessToolRegistry
  dispatcher.py    # InterprocessToolDispatcher
  auth.py          # Authorization header 校验
  default_tools.py # 第一版显式注册的跨进程工具
```

修改：

```text
backend/src/web_app.py
  - 增加 POST /interprocess-tools/call
  - route handler 只做 HTTP 解析、鉴权、调用 dispatcher、返回 JSON
```

如果某个现有工具的 handler 适合跨进程复用，可以在 `default_tools.py` 中显式包装注册。不要在 registry 里自动扫描 `backend/src/tools/`。

## 与 skill 的关系

这个计划只实现后端跨进程入口。让 Agent 知道“有哪些预置脚本、怎么调用脚本”应通过 skill 或长期指令完成。

skill 文档应该描述：

- 脚本路径。
- 脚本用途。
- 简单参数示例。
- 复杂参数使用 `--args-file`。
- 脚本输出的成功/失败格式。

skill 文档不应该包含 token。

## 测试计划

后端单元测试：

- registry 能注册工具并拒绝重复名字。
- dispatcher 找不到工具时返回 `tool_not_found`。
- dispatcher 参数结构错误时返回 `stage="validate"`。
- dispatcher handler 成功时返回 `ok=true`。
- dispatcher handler 抛异常时返回 `ok=false`，且不会把异常变成未处理 500。

HTTP 测试：

- 未配置 token 时 endpoint 不可用。
- 缺少 `Authorization` 返回 `401`。
- Bearer token 错误返回 `401`。
- token 正确但请求 JSON 结构错误返回 `400`。
- token 正确且工具成功时返回 `200` 和 `ok=true`。
- token 正确且工具业务失败时返回 `200` 和 `ok=false`。

回归测试：

- 现有 WebSocket 聊天、会话列表、模型原生工具调用不受影响。

## 实施步骤

1. 新增 `backend/src/interprocess_tools/` 模块，先实现模型、registry、dispatcher。
2. 写 dispatcher 和 registry 的单元测试。
3. 在 `web_app.py` 增加鉴权函数和 `/interprocess-tools/call` route。
4. 写 HTTP endpoint 测试。
5. 新增一个极小的示例跨进程工具用于测试，放在 `default_tools.py`，不要引入真实业务复杂度。
6. 跑后端测试：`cd backend && PYTHONPATH=. uv run --with pytest python -m pytest -q`。
7. 后续如果用户提供真实预置脚本，再补 skill 文档和脚本调用说明。

## 计划自查

- 自顶向下：先定义目标和边界，再定义抽象、接口、模块和测试。
- 不省略关键接口：已明确 registry、dispatcher、HTTP endpoint、鉴权、参数和响应格式。
- 不过度细化：没有写具体代码实现，只约束模块职责和可验证行为。
