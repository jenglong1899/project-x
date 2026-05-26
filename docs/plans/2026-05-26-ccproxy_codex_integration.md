# 接入 ccproxy-api（以 Codex 为目标）的初步方案（计划阶段）

目标：让 project-x 通过你本地运行的 `ccproxy-api` 使用“逆向 Codex”，并且**必须支持 tool call**。

重要前提：你们要用 **GPT-5.2**，而它（按你现在的信息）只能走 **OpenAI Responses API**，因此我们不能只做“OpenAI Chat Completions 兼容”那条接入，必须补齐 Responses API 的调用与流式解析。

非目标：本文件不落地任何代码改动，只描述方案与验证方式。

---

## 0. 先把雷点说清楚（避免做无用功）

1) **对 Codex/Responses：我们必须把自己的 system prompt 下沉到 user**  
CCProxy 的 Codex Response API 文档明确写了：`System prompts/instructions are overridden by the required Codex instruction prompt`。  
也就是说：即使我们继续传 `role=system`，也不应把它当成“可控且会生效”的指令层。  
因此本接入的策略是：

- **Codex/Responses 路径**：用现有的 build_codex_user_instruction（把之前的system prompt融合进了user prompt中）
- **非 Codex 路径**（qwen/deepseek/mock 等）：维持现状，继续用 system + user instruction。

2) **真正的关键不确定性：Codex 侧（Responses API）是否完整支持工具协议**  
project-x 的核心价值是工具链（`bash/read_file/...`）+ 反复回合。  
如果 “逆向 Codex” 在 Responses API 下只支持纯对话、不支持工具（或流式事件/字段不兼容），那接入后会出现：
`assistant` 永远不下工具 → 系统能力大幅退化。  
因此计划里必须先用最小实验确认：

```
能否完成：
user -> assistant(tool_calls) -> tool -> assistant
```

3) **Responses API 的流式事件和 Chat Completions 完全不是一套东西**  
project-x 现在的 `AgentTurn.stream()` 是按 Chat Completions 的 `choices[0].delta.*` 来解析的。  
要支持 GPT-5.2/Responses API，我们需要新增一条“Responses 流式解析”路径，把 Responses 的 event/item 还原成 project-x 现有的：
- `on_ai_content_delta`
- `on_ai_tool_call_*`
这一步是本接入里最核心、也是最容易踩坑的部分。

---

## 1) 初步思路（你先选路线）

### 思路 A：先走 ccproxy 的 Responses API（推荐）

假设：ccproxy 的 `/codex/v1` 提供 OpenAI Responses API，并且工具协议足够兼容。  
做法：
- 新增一个模型配置 key（例如 `PROJECT_X_MODEL_CONFIG=ccproxy-codex`）
- `base_url` 指到 `http://127.0.0.1:8000/codex/v1`
- 在 project-x 增加“Responses API 调用链”：请求打到 `${base_url}/responses`
- `model` / token 全部从 env 读（model 先用占位值，真实映射交给 ccproxy 的 model-mapping layer）

优点：满足 “GPT-5.2 只能 Responses API” 的硬约束；失败也能快速定位是“ccproxy/协议不兼容”还是“我们解析问题”。  
风险：需要我们实现 Responses streaming parser（这是必做工作，不存在“纯配置接入”）。

### 思路 B：在 Responses 路径上加“messages 适配器”（必做，但保持最小侵入）

假设：Codex Response API 下，我们的 `system` 不可控（被覆盖），但工具可用。  
做法：在发请求前做一次 messages 变换（这是“必做适配”，不是可选增强）：

```
[system:S] + [user:U1] + rest
        |
        v
[user: S + "\n\n" + U1] + rest
```

并且把后续所有 `system`（如果历史里混入了）统一下沉到 user（确保“我们自己的规则”不会依赖 system 层）。

优点：对“system 不可用”更鲁棒。  
风险：会引入项目内部的“某些 provider 特判”，要小心别污染默认路径。

### 思路 C：把 ccproxy 当成“额外的模型后端”，但不强行要求工具

假设：你主要用 codex 做“代码解释/建议”，不依赖 project-x 的工具闭环。  
做法：接入后允许关闭工具（或把 tool_calls 当作不支持）。

优点：最容易成功。  
风险：这会偏离 project-x 的“Agent + 工具”定位，价值打折。

---

## 2) 选定路线后的实现思路（细分一层）

### 2.1 统一抽象：把“Chat Completions vs Responses”显式建模 + 指令注入策略

我们保持现有 Chat Completions 调用链不变（给 qwen/deepseek/mock 用）：

```
AgentTurn.stream()
  -> litellm.acompletion(model, api_base, api_key, messages, tools, stream=True)
```

同时新增 Responses API 调用链（给 GPT-5.2/ccproxy-codex 用）：

```
AgentTurnResponses.stream()
  -> POST {base_url}/responses (stream=true)
  -> 解析 SSE/event stream
  -> 回调 on_ai_content_delta / on_ai_tool_call_*
```

并且在进入 `AgentTurnResponses.stream()` 前，对 messages 做一次 Codex 专用适配：

```
messages_for_codex = downcast_system_to_user(messages)
```

新增“ccproxy-codex”配置来源（全部 env 驱动）：
- `PROJECT_X_MODEL_CONFIG=ccproxy-codex`
- `PROJECT_X_CCPROXY_BASE_URL`（默认 `http://127.0.0.1:8000/codex/v1`）
- `PROJECT_X_CCPROXY_MODEL`（默认值只是占位；真实映射优先交给 ccproxy）
- `PROJECT_X_CCPROXY_API_KEY`（默认 `sk-dummy`）

### 2.2 如果选思路 B：增加“messages 适配层”（最小侵入）

建议把适配写成一个纯函数（易测）：

```
def adapt_messages_for_provider(provider, messages) -> messages
```

仅在 `provider == "ccproxy-codex"` 时启用：
- 合并/下沉 system 到 user
- 必要时对 `tool_calls` 做兼容（例如某些实现返回 `function_call` 而不是 `tool_calls`）

### 2.3 tool_calls 的关键验证点（Responses 版本）

我们用一个最小工具（优先复用现有 `bash`）：
- 让模型必须调用工具（例如读取一个文件、或者运行 `pwd`）
- 若能稳定产生 tool_calls，说明可用；否则要么：
  - 放弃用 codex 做“工具型 agent”，退回思路 C
  - 或改用 ccproxy 的其他 provider（比如 Claude SDK/API）承担 agent，codex 仅做辅助

---

## 3) 测试思路（不跑大测试，先做最小闭环）

### 3.1 本地手工验收（最优先）

1) 先单独跑 ccproxy-api（你本地）：确认 `/codex/v1` 可访问  
2) project-x 后端设置：
   - `PROJECT_X_MODEL_CONFIG=ccproxy-codex`
   - `PROJECT_X_CCPROXY_BASE_URL=.../codex/v1`
   - `PROJECT_X_CCPROXY_MODEL=...`
3) 在 UI 发一句强制工具的任务，例如：
   - “请调用 bash 执行 `pwd`，并把结果原样返回”

判定标准：
- 前端能看到 tool 卡片（started/finished）
- tool result 被模型引用
- 失败时不会 500（允许模型自己纠错）

额外判定（针对“system 下沉”）：
- 我们的“原 system 内容”应该出现在 **第一条 user** 里，而不是 system（可通过 request_tracer/raw_http 日志确认）

### 3.2 单元测试（只测我们自己的适配逻辑）

如果启用思路 B：
- 给 `adapt_messages_for_provider()` 写测试：输入含 system 的 messages，输出第一条 user 合并正确
- 给“无 tool_calls 时不崩”的路径写测试（stream 拼装结果）

---

## 4) 我现在需要你确认的一个关键问题

你已经明确：**必须支持 tool call**。  
因此验收标准会更严格：能稳定跑通 `assistant(tool call) -> tool -> assistant` 才算接入完成。
