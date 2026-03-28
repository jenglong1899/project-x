# `reset-context` Tool 实现计划

## 1. 这个需求是否值得做？可能的雷点

**值得做**：当前 system instruction 已经明确告诉 agent 可以使用 `reset_context`，代码里也已经有 `ResetContextDirective` 类型和 `Agent._reset_context()` 这个预留点，但真正行为还没接上。继续让这部分处于“提示存在、实际不可用”的状态，会让记忆机制始终少一条闭环。

**主要雷点**
- **工具结果和编排指令的边界**：`execute_tool_calls()` 现在默认把所有工具结果都落成 `tool` message，然后返回 `ContinueLoopDirective()`。`reset_context` 需要同时做到“给模型一个 tool result”和“给编排器一个 reset 指令”，边界要清楚，不然容易把普通工具也卷进特殊分支。
- **第一次调用不执行**：规格要求第一次调用 `reset_context` 时只返回提醒文本，不真正 reset。这个判断必须基于当前 runtime `messages`，而不是额外落盘一个状态位，否则会引入没有必要的新持久化。
- **重建 instruction 的责任归属**：真正的 reset 不是简单地清空 `_messages`，而是要重新 `build_system_level_instruction_zh()` / `build_user_level_instruction_zh()`，再 `new_conversation()`。如果只调用 `new_conversation()`，会继续复用旧的 instruction 快照，达不到“重新加载 main.md”的目的。
- **新旧会话的语义**：规格写的是 “要 new_session”。这意味着 reset 之后的后续对话要写入新的 conversation 文件，旧会话保留为历史，而不是把新内容继续 append 到旧文件。

## 2. 推荐方案

### 2.1 总体思路

- 新增一个真正注册进 agent 的 `RESET_CONTEXT_TOOL`。
- `reset_context` 没有输入参数。
- `reset_context` 的工具输出只有一个字段：
  - `hint: str | None`
- `execute_tool_calls()` 不真正执行 reset 的业务逻辑。
  - 当它发现 tool call 是 `reset_context` 时，不去调用通用 handler，而是返回一个 `ResetContextDirective`
  - 真正的“第一次调用判断 / 是否 reset / 产出 hint / new session / 自动续跑”都放在 `Agent._reset_context()` 中完成
- `Agent._reset_context()` 负责真正的上下文重置：
  - 判断这是不是当前 runtime 中第一次调用 `reset_context`
  - 第一次调用：不重置，只返回固定提醒文案作为 tool result
  - 非第一次调用：重新 build system/user instruction，开启新 session，并自动注入 `<auto_reminder>...`
- 是否第一次调用 reset，不单独存状态，直接从当前 `_messages` 中查找历史 `reset_context` tool call 次数。

### 2.2 为什么推荐这个方案

- **职责边界更清楚**：`execute_tool_calls()` 负责“识别这是特殊工具并返回指令”，`Agent._reset_context()` 负责“真正改会话状态并决定 hint”。
- **最符合现有结构**：特殊逻辑聚焦在 `execute_tool_calls()` 和 `Agent._reset_context()`，不需要额外引入新的编排层对象。
- **保持 tool message 链路一致**：即使 reset 逻辑不在 `execute_tool_calls()` 里执行，最终仍要由 `_reset_context()` 产出标准 tool result message，让模型下一轮能看到 hint。
- **避免过度工程化**：不新增数据库字段、不新增 conversation metadata、不引入额外 session manager；只在已有编排抽象上补齐行为。

### 2.3 不推荐的方案

**方案 B：在 `execute_tool_calls()` 里直接把第一次调用判断和 reset 逻辑全做完**
- 缺点：会把“特殊工具识别”和“会话重建、副作用、自动续跑”都塞进同一个函数里，边界变糊。
- 缺点：后面如果 reset 还要发前端事件、注入自动 user message，这个函数会继续膨胀。

**方案 C：第一次调用状态单独持久化到 conversation JSON**
- 缺点：这是一次纯 runtime 编排判断，不值得为它污染持久化格式。
- 缺点：规格明确说“发现 messages 中是第一次调用”，说明判断依据就该是 messages。

## 3. 详细设计

### 3.1 新增工具与指令载荷

涉及文件：
- `backend/src/tools/reset_context.py`
- `backend/src/core/agent_turn.py`
- `backend/src/websocket_chat_session.py`
- `frontend/src/features/chat/protocol.ts`
- `frontend/src/features/chat/store.ts`
- `frontend/src/features/chat/client.ts`
- `frontend/src/App.tsx`（或当前承接会话切换的组件）

设计：
- 新增 `ResetContextToolOutput`：
  - `hint: str | None`
- 新增或扩展 `ResetContextDirective`，至少带上：
  - `tool_call_id: str | None`
  - `tool_name: str`

处理规则：
- `reset_context` 没有业务输入，不需要 `prompt_to_my_future_self`
- `execute_tool_calls()` 发现 `reset_context` 后：
  - 校验本轮 tool call 数量是否为 1
  - 不走普通 handler
  - 直接返回 `ResetContextDirective`
- `_reset_context()` 再基于当前 runtime `messages` 判断是不是第一次调用，并生成 `{"hint": ...}` 对应的 tool result

### 3.2 `execute_tool_calls()` 的改动

函数职责调整为：

```text
Agent.run()
  -> execute_tool_calls(ai_msg_dict, tools_by_name, on_tool_result)
       -> 普通工具: 正常执行，返回 ContinueLoopDirective + tool message
       -> reset_context:
            如果和其他工具一起出现，直接报错
            否则返回 ResetContextDirective(tool_call_id, tool_name)
```

这里不做的事：
- 不判断第一次调用与否
- 不生成 reset 的 hint
- 不重建 session

这里必须做的事：
- **硬性禁止并发工具调用**：不能只靠工具描述提醒模型，后端也要校验；如果 assistant 同一条消息里既有 `reset_context` 又有其他工具调用，应直接报错，避免进入不确定状态。

工具描述里仍然要明确写：
- 调用本工具时不能和其他工具一起调用
- 这是一个会切换会话上下文的特殊工具

### 3.3 `Agent._reset_context()` 的改动

涉及文件：
- `backend/src/core/agent.py`
- `backend/src/prompts/builder.py`（只读复用，不新增接口时可不改）
- `backend/src/websocket_chat_session.py`
- `frontend/src/features/chat/protocol.ts`
- `frontend/src/features/chat/store.ts`

行为定义：
- 输入：`ResetContextDirective`
- 先遍历当前 runtime `_messages`，判断这是不是第一次调用 `reset_context`
- 第一次调用：
  - 不做真正 reset
  - 生成固定提醒文案作为 tool result：`{"hint": "...固定长文案..."}`
  - append 对应 tool message
  - 通过现有回调把 tool result 继续透传给前端
  - 返回“未执行 reset”的结果，让 `run()` 继续原会话
- 非第一次调用：
  - 先给旧会话补上一条 tool result，例如 `{"hint": null}` 或一条很短的执行提示
  - 重新 build：
    - `self._system_instruction = build_system_level_instruction_zh()`
    - `self._user_instruction = build_user_level_instruction_zh()`
  - 调用 `self.new_conversation()`
  - 向新 session 注入一条系统生成的 user message：
    - `<auto_reminder>你刚才进行了一次重置上下文操作，请继续进行任务</auto_reminder>`
  - 这条消息需要真正进入 `_messages` / conversation store 流程，以便触发后续模型运行
  - 返回“已执行 reset”的结果，让 `run()` 继续在新会话中 stream

前端感知：
- reset 真正执行时，后端需要发一个显式事件给前端，表示“当前 runtime 已切到一个新的 session”。
- 前端收到后应自动切换到这个新对话。
- 因为 reset 后会立刻注入 `<auto_reminder>` user message，新会话会被持久化，因此这个事件最好能直接带上新的 `conversationId` / `displayName`，避免前端还要等下一拍再切。

结果语义：
- reset 之后，`run()` 的 `while True` 继续下一轮，会基于新的 system/user instruction 和 `<auto_reminder>` 再次调用模型。
- 若用户在工具执行期间已经排队了新的 user message，需要明确和 `<auto_reminder>` 的顺序：
  - 推荐 `<auto_reminder>` 先进入新 session
  - 然后再 drain 用户排队消息

### 3.4 会话持久化与 display-name 语义

reset 前：
- 原 conversation JSON 正常保留，包含触发 reset 的 assistant/tool message

reset 后：
- 调用 `new_conversation()` 后，新的 session 会通过自动注入的 `<auto_reminder>` user message 触发持久化
- 因此 reset 执行后会立刻出现一个新的 conversation 文件
- `display-name` 规则要从“首条 committed 的后续 user message”改成：
  - `system/user instruction` 之外的第一条 `role=user` 消息
- 这条规则要同时覆盖：
  - 用户手动发起的新会话
  - reset 自动开启的新会话

需要注意的副作用：
- 如果完全按上面的规则实现，reset 后新会话的 `display-name` 会来自 `<auto_reminder>...`
- 这会让连续多次 reset 产生相似的会话标题
- 如果这是预期行为，就按这个规则实现；如果不是，后续需要再单独定义“自动提醒消息是否参与 display-name”的规则

这符合当前项目“空会话不落盘”的约束，也符合“要 new_session”的规格。

## 4. 测试计划

### 4.1 `backend/tests/test_agent_callbacks.py`

新增或调整以下测试：

1. `execute_tool_calls()` 遇到普通工具，仍然返回 `ContinueLoopDirective`
- 防止引入 reset 后污染原有行为

2. `execute_tool_calls()` 遇到 `reset_context`
- 如果同轮还有其他工具调用，直接报错
- 如果只有它自己，返回 `ResetContextDirective`

3. 第一次调用 `reset_context`
- `_reset_context()` 不真正 reset
- 产出 1 条 tool message
- tool message 内容是规格里的提醒文本

4. 非第一次调用 `reset_context`
- `_reset_context()` 真正重建 instruction
- 创建新的 conversation store
- 注入 `<auto_reminder>` user message

5. `Agent.run()` 在 reset 发生后会重新 build instruction 并开启新会话
- mock `build_system_level_instruction_zh()` / `build_user_level_instruction_zh()`
- mock `_safe_stream()`，验证第二轮调用收到的是新的前两条 instruction

6. reset 后的新消息不继续写入旧 conversation 文件
- 用临时目录跑 `ConversationStore`
- 触发 reset 前先落一份旧会话
- reset 后通过 `<auto_reminder>` 自动落新会话
- 断言目录里出现第二个 conversation 文件

7. reset 后的 `display-name` 取自 `system/user instruction` 之外的第一条 user message
- 覆盖普通新会话
- 覆盖 reset 自动新会话

8. WebSocket / 前端协议
- reset 真正执行时，后端会发一个前端可识别的“切到新 session”事件
- 前端收到后会自动切换到该新对话

### 4.2 可选补测试

如果实现里把“判断历史 reset 次数”抽成了私有辅助函数，应给它单独补一个小测试，避免后续改 assistant/tool message 结构时静默回归。

## 5. 实施顺序

1. 先补 `reset_context` 工具定义和结果类型
2. 改 `execute_tool_calls()`，让它识别 `reset_context` 并禁止并发工具调用
3. 实现 `Agent._reset_context()`
4. 调整会话持久化 / `display-name` 逻辑，支持 reset 自动新会话
5. 增加 WebSocket 事件与前端自动切换逻辑
6. 在默认 agent 注册 `RESET_CONTEXT_TOOL`
7. 补测试并跑 `uv run pytest -q`

## 6. 假设与边界

- 会新增前端可消费的 reset 事件，用于感知“后端已经切到新 session”，前端效果是自动切换到该新对话。
- 不改 conversation JSON 格式。
- 不实现规格里“隔一定消息数量自动提醒 reset”的功能，本次只实现用户明确要求的 `reset-context tool`。
- 非第一次调用 reset 时，tool result 可以是 `{"hint": null}`，第一次调用的提醒文案应与规格保持一致。
- reset 工具描述里会写明“不能和其他工具一起调用”，但后端也会做硬校验，不能只靠提示词约束。

## 7. 计划自检

这份计划已经明确到以下程度：
- 新增哪些模块
- 哪一层负责判断“第一次调用”
- 哪一层负责真正重建 instruction / new session
- 测试要覆盖哪些行为边界

同时没有细到直接把每个函数体逐行写死，实施时仍保留了合理的命名和局部结构选择空间，粒度合适。
