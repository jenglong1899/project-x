# `reset_context` tool 实现计划

## 目标

实现模型可调用的 `reset_context` 工具，用来“重置上下文”：重新 build system/user instruction，并开启一个新会话（new_session）。**第一次调用只提示不执行**；第二次及以后才真正执行重置，并自动注入 `<auto_reminder>...` 让模型继续原任务。

## 行为定义

工具：
- 名称：`reset_context`
- 入参：无
- tool result（给模型看的 JSON）：`{"hint": string | null}`
- 约束：同一条 assistant message 的 `tool_calls` 里，`reset_context` 必须是唯一工具；不能和其他工具并发。后端需要硬校验，违规直接报错。

第一次调用（当前 runtime messages 中从未出现过 `reset_context`）：
- 不执行重置
- 返回固定提醒文案作为 tool result（见下方原文）
- 会话继续保持不变

非第一次调用：
- 不需要append对应的tool call，因为不需要在这个上下文中进行下一步的工作了
- 重新 build instruction（system + user），并开启新会话（new_session）
- 在新会话中自动注入一条系统生成的 user message：
  - `<auto_reminder>你刚才进行了一次重置上下文操作，请继续进行任务</auto_reminder>`
- `run()` 继续循环：先处理 auto_reminder，再按队列顺序处理用户已排队的消息

第一次调用的固定提醒文案（tool result 的 `hint` 字段内容，原样使用）：
```
维护记忆文档非常重要，由于这是当前上下文中你第一次调用本工具，当前调用不会执行重置操作，请先检查：
- 重置上下文的时机：对上下文做了摘要后，大概是原始内容的50%以内吗？
- 记录进记忆文档的信息：
    - 当前记录了的内容能否保证你重置记忆以后还能像之前那样继续工作，仿佛这个重置什么没发生过一样？
    - 人类会记住哪些内容？
        - 人类不会记住“一小时前执行了ls命令”这种无关紧要的信息
        - 人类犯了一个错误就会记录下来，避免以后再犯
        - 人类会记得一个文件大概讲了什么
        - 人类会记住自己的 todo list
- 当前文档的结构是整洁有序的吗？

这些值得你花时间去检查、处理，你要注重质量，而不是速度。

下一次调用本工具时将会执行重置上下文操作。
```

## 后端实现

关键点：`execute_tool_calls()` 只负责“识别 reset_context 并返回编排指令”，真正的“是否第一次调用/是否执行重置/如何 new_session/注入 auto_reminder”放在 `Agent._reset_context()`。

涉及关键文件（按优先级）：
- `backend/src/tools/reset_context.py`（新增）
- `backend/src/core/agent_turn.py`
- `backend/src/core/agent.py`
- `backend/src/conversation_store.py`（仅当需要避免 auto_reminder 变成 display-name 时才改）

### 1) 新增工具定义

- 新增 `RESET_CONTEXT_TOOL`（ToolSpec），名称为 `reset_context`，无入参 schema。
- 工具描述需要写清楚不能和其他工具并发调用

### 2) `execute_tool_calls()` 特判

在 `execute_tool_calls(ai_msg_dict, tools_by_name, on_tool_result)` 中增加分支：
- 如果发现 `reset_context`：
  - 校验本条 assistant message 的 tool_calls 数量必须是 1，且工具名就是 `reset_context`；否则直接抛错
  - 不走通用 handler（不执行普通工具路径）
  - 返回 `ResetContextDirective(tool_call_id, tool_name="reset_context")`

### 3) `Agent._reset_context()` 真正执行 reset

`Agent.run()` 处理 `ResetContextDirective` 时调用 `_reset_context()`，它需要：
- 在 append 本次 tool message 之前，通过扫描当前 runtime messages 判断是否为第一次调用：
  - 以“历史是否已经出现过 reset_context tool message”作为主判断
  - 为了更稳健，可同时兼容扫描 assistant message 的 tool_calls（防止未来消息结构调整）
- 第一次调用：
  - 只 append 一条 tool message（`{"hint": 固定长文案}`），不重置
- 非第一次调用：
  - 重建 instruction：`build_system_level_instruction_zh()` + `build_user_level_instruction_zh()`
  - `new_conversation()` 开启新会话，并确保新会话对应的 conversation JSON 已可被加载（例如：立即落一个空会话文件）
  - 通过 WebSocket 发出 `reset.context({conversationId, displayName})`（见下文），让前端先完成自动切换
  - enqueue `<auto_reminder>...` 到新会话，让 `run()` 继续循环并推进任务

### 4) 会话持久化与 display-name（最小改动）

目标：reset 后必须产生一个新的 conversation（新文件），旧会话保留为历史。

关于 `display-name`（建议的最小规则）：
- reset 新建的会话，`meta.display-name` 直接继承旧会话的 `display-name`（保证侧栏连续性）
- 生成的 `<auto_reminder>...` 不参与 `display-name` 推导

## WebSocket 与前端对接（尽量复用现有机制）

新增一个 WebSocket 事件：`reset.context`，用于明确通知前端“已切到新会话”，从而前端可以**立即**自动切换到新的 conversation 文件（而不是等待 `conversation.persisted`）。

事件：
- 名称：`reset.context`
- 触发时机：仅在“非第一次调用 reset_context 且真正执行重置”时触发一次
- payload（建议）：
  - `conversationId: string`（新会话 id）
  - `displayName: string`（新会话侧栏展示名；建议继承旧会话 display-name）

时序约束（关键）：
- `reset.context` 必须在新会话的任何事件（例如 `user.message.committed` / `agent.became.busy` / `assistant.message.*` / `tool.*` / `conversation.persisted`）之前发送给前端。
- 为保证“切换到新的 conversation 文件”语义成立，后端需要在发送 `reset.context` 前就确保新 `conversationId` 对应的 conversation JSON 已可被加载（最简单是：reset 时立即落一个空会话文件，后续再正常追加 messages）。

前端处理：
- 收到 `reset.context` 后：
  - 将 `activeConversationId` 切换到新的 `conversationId`
  - 清空/切换主时间线到新会话（复用现有“切换会话”的内部逻辑即可）
  - 更新侧栏会话列表（插入或更新该会话；若后续仍会收到 `conversation.persisted`，可视为“二次校准”，不再承担“触发切换”的职责）

## 测试计划

后端（pytest）：
- `execute_tool_calls()`：
  - 普通工具不受影响
  - `reset_context` 单独出现时返回 `ResetContextDirective`
  - `reset_context` 与其他工具并发时抛错
- `_reset_context()`：
  - 第一次调用：不 new_session，只返回固定 hint，并落一条 tool message
  - 第二次调用：会 new_session，重建 instruction，并注入 `<auto_reminder>...`
- 持久化：
  - reset 前后产生两个 conversation 文件，新消息不会继续写到旧文件
  - reset 新会话的 `meta.display-name` 继承旧会话，且 `<auto_reminder>...` 不会改写 display-name

WebSocket（后端已有的 websocket 测试体系里补）：
- reset 真正执行后会先发出 `reset.context({conversationId, displayName})`
- `reset.context` 必须先于新会话的任何后续事件
- 后续生成发生在新 `conversationId` 语义下（前端已切换，不会把新会话事件投到旧时间线）

## 实施顺序

1. 增加 `RESET_CONTEXT_TOOL`
2. `execute_tool_calls()` 特判并返回 `ResetContextDirective`
3. 实现 `Agent._reset_context()`
4. 补持久化与 display-name 的最小必要逻辑
5. WebSocket 新增 `reset.context` 事件，并让前端自动切换
6. 补测试并跑 `uv run pytest -q`
