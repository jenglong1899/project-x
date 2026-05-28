# Memory Manager 唤醒阈值改造（固定刻度 + 无工具也触发）

## 背景与目标
- 现状：`Agent._maybe_wake_memory_manager()` 仅在“工具执行后”被调用；且阈值是“相对上次 checkpoint 增长 3% 才唤醒”。
- 目标：
  1) 阈值改为固定刻度：上下文剩余 `97/94/91/88/...`（等价于已使用 `3/6/9/12/...`），“跨过任意一个刻度就唤醒一次”。
  2) 即使整段对话完全不触发工具，也能唤醒 memory manager。

## 关键抽象
- `last-triggered-threshold`：持久化到 `ConversationStore.meta["memory-manager"]` 的一个整数，表示“上一次触发唤醒时，已使用上下文百分比的刻度值”，取值为 `0, 3, 6, 9, ...`。
  - 说明：用户表述的“剩余 97/94/...”是倒序刻度；实现时用“已使用百分比刻度”更直观。

## 实现思路（自顶向下）
1) 阈值计算（固定刻度）
   - 计算 `used_ratio = current_tokens / context_limit`。
   - 计算 `used_percent = floor(used_ratio * 100)`。
   - 计算 `current_threshold = (used_percent // 3) * 3`（归一到 3% 刻度）。
   - 若 `current_threshold > last_triggered_threshold`：触发唤醒，并把 `last_triggered_threshold` 更新为 `current_threshold`（一次可能跨多个刻度，但只唤醒一次）。

2) 状态持久化（替换旧字段）
   - 将 `ConversationStore` 里的 `last-checkpoint-tokens` 替换为 `last-triggered-threshold`。
   - 不做向后兼容：老会话缺字段时按 `0` 处理即可（下一次检查会在首次跨过 3% 刻度时触发）。

3) 调用时机（无工具也触发）
   - 在 `Agent.run()` 中新增唤醒检查点：
     - 每次 `assistant` 消息 append/persist 后（即本轮模型调用结束后）调用一次 `_maybe_wake_memory_manager()`。
     - 保留原来“工具执行后”的调用点（或合并成“每次 append 一批消息后统一检查一次”，以减少重复计算）。

## 风险与边界
- token 统计当前是估算口径（按字符/4）；阈值刻度是启发式，允许轻微抖动。
- 若一次输出很长，可能从阈值 `3` 跳到 `12`，设计上只唤醒一次（符合“越过任意阈值就唤醒”的要求）。

## 测试思路
- 单元测试（推荐）：
  - 为阈值计算写纯函数测试：给定 `context_limit/current_tokens/last_triggered_threshold`，断言是否触发、触发后阈值更新值。
  - 为 `ConversationStore` 的 meta 序列化/反序列化写测试：缺字段默认值为 `0`，更新后会写入 JSON。
- 集成测试（次选）：
  - 构造一个“无工具调用”的对话回合，断言 `_maybe_wake_memory_manager()` 会被调用（可通过日志/计数器/状态字段变化验证）。

