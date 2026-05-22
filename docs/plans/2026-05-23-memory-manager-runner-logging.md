# Memory Manager（summary/judge）工作过程日志

目标：用日志记录 memory manager 的工作过程，**summary / judge 分开记录**；日志文件名为：

```
{conversation_file_name前6位}{summary|judge}{第几轮唤起}.jsonl
```

日志目录固定为：`~/.project-x/memories/logs/`

只记录 3 类消息：`user msg`、`ai msg`、`tool msg`。

## 雷点与约束

1) **文件名冲突 / 覆盖**
- summary/judge 都可能被多次唤起；judge 甚至可能在 summary 未结束时再次被唤起。
- 解决：为 summary 与 judge **各自维护独立的 awaken 计数器**，并在唤起前原子递增，确保文件名唯一。

2) **日志体积**
- 不把 worker 的全量上下文（worker_messages_snapshot）原样写进日志，否则极易爆盘。
- 解决：日志只记录 runner 自己“追加”的消息（注入的 user prompt、每轮 assistant、每次 tool result）。

3) **并发写**
- summary/judge 各写各的文件，单文件只会被单个 task 写，避免锁复杂化。

## 实现思路（自顶向下）

### 需要的抽象

新增一个极薄的 JSONL logger：

- `MemoryManagerRunLogger`
  - 入参：`conversation_file_name`、`runner_kind in {"summary","judge"}`、`awaken_round`（本 runner 第几轮唤起）
  - 负责：创建 `~/.project-x/memories/logs/`、打开目标文件、`append_event(event: dict)`（一行 JSON）

事件统一结构（JSONL 每行一个 object）：

```
{
  "ts": "2026-05-23T12:34:56.123Z",
  "kind": "user_msg" | "ai_msg" | "tool_msg",
  "role": "user" | "assistant" | "tool",
  "content": "...",            # 有则写；若存在 reasoning_content，则拼到 content 前面
  "tool_calls": [...],         # ai_msg 有则写（原样）
  "tool_call_id": "...",       # tool_msg 有则写
}
```

### 计数器（第几轮唤起）的来源

在 `ConversationStore.meta["memory-manager"]` 下**只保留**下面两个字段（不考虑向后兼容；旧字段会删除）：

- `summary-awaken-count`
- `judge-awaken-count`

并暴露：

- `ConversationStore.memory_manager_summary_awaken_count`
- `ConversationStore.memory_manager_judge_awaken_count`
- `ConversationStore.update_memory_manager_state(summary_awaken_count=..., judge_awaken_count=...)`

同时删除旧字段 `awaken-count`（以及相关常量/属性/读写逻辑），避免同一个状态被多处维护导致含义漂移。

### 在哪里打日志

1) `backend/src/core/agent.py::_maybe_wake_memory_manager`
- 在创建 `summary_task` 前：
  - `summary_round = conversation_store.memory_manager_summary_awaken_count + 1`
  - 先 `conversation_store.update_memory_manager_state(summary_awaken_count=summary_round, ...)`
  - 然后把 `summary_round` 传给 `MemoryManagerSummaryRunner.run(..., awaken_round=summary_round, conversation_file_name=...)`
- judge 同理使用 `judge-awaken-count`。

2) `backend/src/core/memory_manager.py`
- `MemoryManagerSummaryRunner.run(...)`：
  - 创建 `MemoryManagerRunLogger(kind="summary", awaken_round=...)`
  - 每次往 `forked_messages` append 新消息时，同时写一条日志：
    - 注入的 user prompt：`kind=user_msg`
    - `stream(...)` 返回的 assistant_message：`kind=ai_msg`
    - `execute_tool_calls(...)` 返回的每条 tool_message：`kind=tool_msg`
  - 记录 assistant 时，将 `reasoning_content`（若存在）拼到 `content` 前面（中间用换行分隔）；日志里不再出现 `extra`
- `MemoryManagerJudgeResetContextRunner.run(...)`：
  - 同上，但 judge runner 的 tool_msg 是“不可执行的 tool result”，也照样记（便于定位模型触发了什么 tool_calls）。

### ASCII 数据流

```
Agent._maybe_wake_memory_manager
  ├─ start summary_task? ──► SummaryRunner.run
  │                          ├─ log user prompt
  │                          ├─ log assistant msg
  │                          └─ log tool msg(s)
  └─ start judge_task? ───► JudgeRunner.run
                             ├─ log user prompt
                             ├─ log assistant msg
                             └─ log tool msg(s, fake)
```

## 测试思路

### 单测（推荐加在 backend/tests/）

1) `MemoryManagerRunLogger` 写入格式
- 用 `tmp_path` 指向一个临时目录（通过给 logger 注入 base_dir 或 monkeypatch `Path("~/.project-x/...")` 的解析点）
- 断言：
  - 目录被创建
  - 文件名符合 `{prefix}{kind}{round}.jsonl`
  - 每行都是合法 JSON，且包含 `kind/role/ts`

2) awaken-round 递增与持久化
- 创建 `ConversationStore`（用 `originals_dir=tmp_path/...`）并 `start_with_first_user_message`
- 调用 `update_memory_manager_state(summary_awaken_count=1, judge_awaken_count=1)` 后落盘
- `load_from_conversation_file_name` 读回，计数器保持一致

3) runner 写日志（最小集成）
- 用 stub 的 `stream/execute_tool_calls`（或 monkeypatch）让 summary/judge 走固定路径：
  - 一次 assistant 无 tool_calls 结束
  - 一次 assistant 带 tool_calls + 返回 tool_msg
- 断言日志行数与 kind 顺序满足预期（至少包含 1 条 user_msg + 1 条 ai_msg；有工具时包含 tool_msg）

### 手工验收

- 本地跑一次对话，触发 memory manager（通过降低阈值或构造长上下文）。
- 验证 `~/.project-x/memories/logs/` 下出现：
  - `xxxxxxsummaryN.jsonl`
  - `xxxxxxjudgeM.jsonl`
- 用 `rg` 搜索 `PROJECT-X-RESET-CONTEXT` 或特定 tool 名，确认日志可用。
