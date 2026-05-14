## 背景与目标

我们要把“记忆机制”的 memory manager 从单一 runner（旧：`build_memory_forked_subagent_prompt`）拆成两个并行触发的 runner：

- `summary runner`：prompt 改为 `build_memory_manager_summary_prompt(...)`，负责维护 `MAIN.md` 等记忆文档。
- `judge runner`：prompt 为 `build_memory_manager_judge_whether_reset_context_prompt(...)`，只判断是否需要 reset-context。

额外约束：

1. `summary` 与 `judge` 在同一个触发点 **同时启动**。
2. `summary` 可能运行很久；若到下一个触发点时上一个 `summary` 仍未结束，则本次触发点 **跳过启动新的 summary**（保证任意时刻最多只有一个 summary 在跑）。
3. `judge` 若判断需要 reset-context，则 **必须等待当前正在跑的 summary 结束** 才能 reset。
4. reset-context 时要 **保留 worker 最新的 10 条消息**。
5. `{WAKE_MEMORY_MANAGER_FLAG}` 的注入时机改为：**summary 开始跑之后立刻插入**（不是等 summary 完成后再插入）。

## 总体设计（高层）

触发点：仍由 `Agent._maybe_wake_memory_manager()` 按 `memory_manager_turn_interval` 计数触发。

在触发点 T：

- 若当前没有 `summary_task` 或已完成：
  - 立即将 `{WAKE_MEMORY_MANAGER_FLAG}` 作为一条 `user` 消息写入 worker conversation（append + persist）。
  - 启动新的 `summary_task = summary_runner.run(...)`。
- 无论是否启动 summary，都启动一次 `judge_task = judge_runner.run(...)`。

并发与等待规则：

- `summary_task`：全局唯一（Agent 级别保存 task 引用），新触发点不会重复启动。
- `judge_task`：每次触发点都可以启动一次（但其 reset 行为会被“等待 summary 完成”门控）。
- 当 `judge_task` 判定要 reset 时：
  - 若存在正在运行的 `summary_task`，先 `await summary_task`。
  - 然后执行 reset-context（保留最近 10 条消息）。

## 数据流/时序（ASCII）

触发点 T:

worker loop
   |
   +--> maybe_wake_memory_manager()
         |
         |--(if no summary in-flight)--> inject WAKE_FLAG msg
         |--(if no summary in-flight)--> start summary_task (async)
         |
         +--> start judge_task (async)
                |
                +--> if judge says reset:
                       await summary_task (if running)
                       reset_context_keep_last_10()

## 关键实现点（落到代码层的抽象）

### 1) runner 拆分

`backend/src/core/memory_manager.py`

- `MemoryManagerRunner` 改名或替换为 `MemoryManagerSummaryRunner`（实现 `MemoryManagerRunnerBase`）。
  - fork worker messages -> append user prompt：`build_memory_manager_summary_prompt(is_first_time_awaken=...)`
  - 执行 stream + tool loop（与现有一致）
  - 返回值：不再依赖 `RESET_CONTEXT_MAGIC_WORD`，因为 reset 判断交给 judge runner；可以保留 `MemoryManagerResult(requested_reset_context=False)` 或定义更明确的 result（例如 `MemoryManagerSummaryResult`）。
- 新增 `MemoryManagerJudgeResetContextRunner`：
  - fork worker messages -> append user prompt：`build_memory_manager_judge_whether_reset_context_prompt(messages=...)`（参数是否需要 messages 视函数签名而定）
  - 执行 stream（通常不需要 tools；但为了复用也可允许 tools）
  - 根据 assistant content 是否包含 `RESET_CONTEXT_MAGIC_WORD` 生成 `requested_reset_context`。

### 2) Agent 侧并发控制

`backend/src/core/agent.py`

新增状态：

- `self._memory_manager_summary_task: asyncio.Task[None] | None`
- `self._memory_manager_judge_runner: MemoryManagerRunnerBase`（或更具体类型）

触发点逻辑：

- 到达 interval 时：
  - 如果 summary_task is None 或 done：
    - 注入 WAKE_FLAG user msg（注意：必须 append 到 `_messages` 并 persist 到 conversation）
    - 启动 summary_task
  - 启动一次 judge（可以直接 await，也可以 task 化；但需要拿到结果决定是否 reset）
  - 若 judge 要 reset：等待 summary_task 完成后再 reset

### 3) reset-context 保留最新 10 条消息

修改当前的 reset-context

- 输入：`keep_last_n_worker_messages=10`
- 实现：
  - 从当前 `_messages` 中截取 worker “业务消息”尾部 N 条（排除 开头的两个system/user instruction 两条）
  - 执行 reset-context：重建 system/user instruction + 新 conversation_store。
  - 将上述消息按原样 append/persist 到新 conversation（确保新 conversation 已经有 first user message 以创建文件
  - 不再使用 `RESET_CONTEXT_AUTO_REMINDER`（因为最后一条消息可能是 tool call，这时候再append一条user msg会导致llm api报错）

注意点/假设（已确认）：

- “最新 10 条 msg”指 `_messages[2:]` 的最后 10 条（包含 `user/assistant/tool`），**不做任何限制**：
  - 最后一条可以是 `assistant(tool_calls)`（未执行完的工具调用）。
  - 也可以是 `tool` result。
  - 也可以是普通 `assistant`/`user`。
- 因此 reset-context 后的新会话可能一开始就处于 `backlog_tool_execution` / `backlog_tool_followup` 状态；这是预期效果，Runner 会继续把工具/后续补完。

## 测试思路（最小）

建议新增/调整 `backend/tests/` 中与 memory manager 相关的单测（若当前无对应测试则新增）：

1. **并发互斥**：summary task in-flight 时再次触发，不会启动第二个 summary（可用 mock runner + 手动控制 await）。
2. **judge 等待 summary**：judge 判定 reset 时，必须先等待 summary 完成后才调用 reset（用事件/flag 断言顺序）。
3. **WAKE_FLAG 注入时机**：触发点启动 summary 后立刻写入一条 user msg（assert `_messages` 末尾出现 flag 且持久化被调用）。
4. **reset 保留 10 条**：构造 `_messages`，触发 reset 后新 conversation 里包含末尾 10 条消息
