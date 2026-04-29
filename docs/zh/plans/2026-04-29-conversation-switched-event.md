# conversation.switched 事件实施计划

## 背景与目标

现在后端 WebSocket 建立时会自动恢复最新 conversation JSON；找不到才 `new_conversation()`。但前端没有收到“当前 conversation segment 已切换，以及用户可见历史是什么”的事件，所以恢复成功后 UI 仍是空的。

目标：用统一的 `conversation.switched` 事件表达“后端当前 conversation segment 已切换”，覆盖两类场景：

- WebSocket 初始连接：恢复最新 segment 或创建新 segment。
- memory manager reset-context：切到新 segment，并通过可见历史继续显示 auto reminder。

## 核心抽象

`conversation.switched` 是适配层事件，不是新的产品能力；前端仍不暴露历史会话列表/切换入口。

```text
WS connected
  ↓
backend activates segment
  ↓
conversation.switched { visibleMessages[] }
  ↓
frontend replaces timeline
  ↓
后续 assistant/tool/user 流式事件继续 append
```

事件语义：

- 收到事件后，前端清空 `items` / `pendingUserMessages` / `errorMessage`。
- 如果 `visibleMessages` 非空，前端把它投影成平铺时间线。
- 如果是新空 segment，`visibleMessages` 为空。
- reset-context 不再单独发 `reset.context`；后端发 `conversation.switched`，其 `visibleMessages` 包含 auto reminder。

## 后端实现思路

- 将 `Agent` 的 `on_reset_context` 回调替换为更通用的 `on_switch_conversation`，参数只包含 `visible_messages`。
- `Agent.resume_conversation()` 成功加载历史后触发 `on_switch_conversation`，让初始 WS 恢复能 hydrate 前端。
- `Agent.new_conversation()` 创建空 segment 时也触发该回调，此时 `visible_messages` 为空。
- 后端构造 `visible_messages` 时只过滤 system instruction 和隐藏 user instruction，其他历史消息保留给前端投影。
- `WebSocketChatSession` 把回调投影为 `conversation.switched`；reset-context 分支复用同一回调，不再额外发送 auto reminder 的 `user.message.committed`。

## 前端实现思路

- 在 `protocol.ts` 增加 `conversation.switched` schema，删除 `reset.context` schema。
- 在 store reducer 中处理 `conversation.switched`：用事件 `visibleMessages` 一次性重建 `items`。
- 历史消息投影逻辑需要理解后端传来的可见历史消息；不会接触 system instruction 和隐藏 user instruction。
- 保持后续流式事件追加逻辑不变，避免恢复历史与正在流式生成互相覆盖。

## 测试思路

- 后端单测：WS 创建时如果找到最新 conversation file，会先发送 `conversation.switched` 且包含历史消息。
- 后端单测：没有历史文件时，会发送空 `conversation.switched`，随后正常处理第一条用户消息。
- 后端单测：reset-context 时不再发送 `reset.context`，而是发送包含 auto reminder 的 `conversation.switched`。
- 前端单测：`conversation.switched` 会替换时间线，并且后续 delta 能继续 append。
