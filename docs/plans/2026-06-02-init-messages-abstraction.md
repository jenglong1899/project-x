# init_messages 抽象计划

目标：把会话启动提示词从 `system_instruction + user_instruction` 二元组，改成统一的 `init_messages` 列表；让 `openai-codex` 可以自然使用“只有 user instruction”的布局，同时去掉各层对“前两条消息固定语义”的隐式依赖。

## 假设

- 不兼容旧 conversation 文件；这次改动后，本地旧会话如果继续按旧格式恢复，允许直接失败。
- `init_messages` 是唯一真源；`system_instruction` / `user_instruction` 从运行时代码中彻底移除。

## 实现思路

### 1. 统一启动消息抽象

新增一个集中构造入口，按 provider 返回启动消息：

```text
litellm        -> [system, user]
openai-codex   -> [user]
```

要求：
- `websocket_chat_session` 不再自己拼 `system_instruction` / `user_instruction`
- `Agent` 构造参数改为 `init_messages`
- `reset_context` 重新加载的也是 `init_messages`

### 2. ConversationStore 去掉前两条硬编码

当前格式隐含：

```text
messages = [system, user_instruction, ...visible_messages]
```

改成：

```text
messages = [*init_messages, ...runtime_messages]
```

要求：
- `start_with_first_user_message()` 和 `start_with_messages()` 都基于 `init_messages` 落盘
- `build_messages_from_history()` 保持原样返回完整消息
- 恢复历史时不再尝试兼容旧格式，只接受新格式

### 3. Agent 的“可见消息边界”显式化

不要再写死 `messages[2:]`。

建议：
- 在 `Agent` 内保存 `init_message_count`
- `visible_messages = messages[init_message_count:]`
- reset/load/start new conversation 都通过同一套边界规则计算

这样数据流会变成：

```text
provider -> build_init_messages()
         -> Agent(init_messages)
         -> ConversationStore(init_messages)
         -> messages = init_messages + runtime
         -> visible = messages[len(init_messages):]
```

### 4. 新格式恢复规则

恢复逻辑只接受新格式，不再做旧布局识别。

要求：
- 会话文件需要显式保存 `init_messages`
- load 时直接从文件恢复 `init_messages`
- 缺失 `init_messages` 或格式不合法时直接报错

## 测试思路

1. `ConversationStore` 单测
- 新建 litellm 会话时，落盘前缀是 `[system, user]`
- 新建 codex 会话时，落盘前缀是 `[user]`
- `start_with_messages([])` 在两种 provider 下都能正确落盘
- 缺失 `init_messages` 的旧格式文件会报错

2. `Agent` 单测
- `_visible_messages_from` 或对应新逻辑能根据 `init_message_count` 正确裁剪
- `_reset_context` 后 `self._messages` 与 provider 对应
- `start_conversation -> reset_context -> resume` 在 codex 下不再生成 `system` 消息

3. WebSocket/集成测试
- `openai-codex` 配置下，首次连接后发送消息，模型输入不含 `system`
- `conversation.switched` 对前端仍只暴露可见消息，不把 init prompt 泄露到 UI

## 风险

- 如果只改 `Agent` 不改 `ConversationStore`，reset 和恢复新会话会继续不一致。
- memory manager 依赖完整 `worker_messages`，不能因为前缀重构误删启动消息。
