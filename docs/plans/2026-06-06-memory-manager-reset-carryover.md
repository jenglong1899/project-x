# Memory Manager reset carryover 计划

## 判断

我同意优先做“方法 2”，因为问题的根因不是“最近 N 条消息不够智能”，而是“最近一次 summarizer 之后到 reset 判定返回之间，存在一段天然未摘要区间”。

`keep-last-n` 的主要雷点：

- 它保留的是“窗口”，不是“边界之后的全部增量”，容易抖动。
- 一旦最近 N 条里刚好有大块文件内容，judge 会持续看到一坨未压缩上下文，reset 可能反复触发。

## 实现思路

### 抽象

把最近一条 `WAKE_MM_SUMMARY_FLAG` 视为“summarizer 已覆盖边界”。

```text
已摘要历史 | WAKE_MM_SUMMARY_FLAG | 未摘要增量
                              ^ judge=true 时要整体搬过去
```

需要新增一个 reset carryover 抽象，语义是：

- 内容：最近一次 summarizer flag 之后的全部 worker 消息
- 生命周期：judge 判定 reset 后生成，reset 后写入新会话，下一次 reset 前可继续累积/覆盖

### 大概改法

- `backend/src/conversation_store.py`
  - 在 conversation meta 里新增 `reset-carryover-messages`
  - 提供读写接口，保证 reset 过程和恢复历史会话时都能拿到这段状态

- `backend/src/core/agent.py`
  - 新增“查找最近一条 summarizer flag 后所有消息”的辅助函数
  - 在 `_handle_memory_manager_reset_request()` 中执行：
    1. `request_pause()`
    2. 等 worker 真正进入 paused
    3. 计算 `flag -> now` 的未摘要增量
    4. 持久化到 conversation store 的 `reset-carryover-messages`
    5. `_reset_context()` 时把这段消息直接作为新会话初始业务消息写入
  - reset 完成后清空旧会话上的 carryover，并把新会话的 threshold 置 0

- `_reset_context()` 的边界
  - 不再只创建“空新会话”
  - 改为创建“init_messages + carryover 消息”的新会话
  - 这样后续 runner 会自然接上 `assistant(tool_calls)` / `tool` backlog

### 假设

- judge 从发起到返回通常只会让 `flag -> now` 多增长一小段，所以直接搬运原文比再次同步 summarizer 更便宜。
- 如果 `flag -> now` 极大，先接受一次较重 reset；后续由下一轮 summarizer 再压缩，不先为低频极端情况引入更复杂分支。
- 没有找到 summarizer flag 时，兜底保留全部业务消息；这是保守策略，避免静默丢上下文。

## 测试思路

- `Agent` 单测
  - judge=true 后，worker 会先进入 paused，再执行 reset
  - reset 后的新会话包含最近一条 `WAKE_MM_SUMMARY_FLAG` 之后的全部消息，不包含 flag 本身
  - 末尾是 `assistant(tool_calls)` 或 `tool` 时，reset 后仍能被 `drive_decision()` 正确续跑
  - 无 flag 场景走“保留全部业务消息”兜底

- `ConversationStore` 单测
  - `reset-carryover-messages` 可持久化、可恢复、默认值为空

## 不做

- 这次不引入“再补一次同步 summarizer”
- 这次不做按 token 大小切分 carryover
