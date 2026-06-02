# Memory Manager reset 改造计划

## 目标

去掉 `keep_last_n` 这类近似策略。改为：

1. 用 `WAKE_MM_SUMMARY_FLAG` 替换 `WAKE_MEMORY_MANAGER_FLAG`
2. `summary` 启动时插入 `WAKE_MM_SUMMARY_FLAG`
3. `judge` 决定 reset 后，先暂停 worker
4. 再对 `flag -> 当前` 这段做一次收尾 summary
5. reset 时不再保留 `flag` 之后的 worker messages，直接开启精简后的新上下文

这样 reset 依赖“summary 已经覆盖完 `flag -> 当前` 的增量”，而不是继续靠 `keep_last_n` 猜测该保留什么。

## 实现思路

### 抽象

把 `WAKE_MEMORY_MANAGER_FLAG` 明确当成一个“summary 覆盖边界”：

```text
旧消息 | summary snapshot
------| WAKE_MM_SUMMARY_FLAG | 未摘要增量
                           ^ judge=true 后先补摘要，再 reset
```

`Agent` 负责三件事：

1. 在正常阈值唤醒时启动后台 `summary/judge`
2. 在 `judge=true` 时，串行完成“补摘要 -> reset”
3. reset 后把阈值状态重置到新上下文当前值，避免立刻再次 wake

### 大概改法

- `backend/src/core/agent.py`
  - 把 `WAKE_MEMORY_MANAGER_FLAG` 全量替换为 `WAKE_MM_SUMMARY_FLAG`
  - 删除 `keep_last_n` reset 逻辑，不再把旧 worker messages 搬进新上下文
  - `judge=true` 后不直接复用旧 summary 结果，而是基于当前 `self._messages` 再跑一次同步 summary，覆盖 `flag -> now`
  - 同步 summary 前先停 worker 继续推进；summary 失败只打日志，不阻断 reset
  - reset 后仍然按新上下文 token 使用量初始化 `last_triggered_threshold`

- `backend/src/core/memory_manager.py`
  - 保持“最近一条 flag 之前的内容已经被摘要过”的提示语义一致
  - 同步把提示文案里的旧常量名改成 `WAKE_MM_SUMMARY_FLAG`

- `backend/src/commons.py`
  - 删除 `WAKE_MEMORY_MANAGER_FLAG`
  - 新增 `WAKE_MM_SUMMARY_FLAG`

- 可能需要的小辅助函数
  - 查最近一条 summary flag 的位置
  - 截取 `flag -> 当前` 这段增量，喂给收尾 summary
  - 触发一次“面向当前 messages 的收尾 summary”

### 假设

- `flag -> 当前` 的增量在 reset 前会被补摘要，因此 reset 后不需要再保留原文
- reset 后的新上下文仍只包含新的 system/user instruction，以及后续继续运行所需的最小消息集合

## 测试思路

- `backend` 单测优先覆盖 `Agent`
  - `judge=true` 时，收尾 summary 的输入覆盖“最近一条 `WAKE_MM_SUMMARY_FLAG` 之后到当前”的完整增量，然后再 reset
  - 增量只有 `user` 消息时，行为与混合 `assistant/tool/user` 序列时保持一致
  - reset 后 `last_triggered_threshold` 会更新，避免下一轮立刻重复 wake

- 回归验证
  - 没有 flag 时的 reset 行为要有明确兜底，避免异常丢消息
  - 原有“纯聊天也会 wake memory manager”的路径不被破坏
