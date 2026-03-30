# Reminder v1（让 Agent 具备“主动性”的定时唤醒）

## 背景
我们希望 Agent 能“提醒自己做事”：Agent 在对话中通过工具创建一个 reminder；到达触发条件后，系统向 Agent 注入一条特殊的 user message，驱动 Agent 再次进入 `run()`，从而实现主动性。

本计划只覆盖 v1：`create_reminder` + `delete_reminder` 两个工具与最小可用的调度与投递链路。

---

## 目标（Goals）
- 提供工具 `create_reminder` / `delete_reminder`，仅供 Agent 调用。
- Reminder 触发时，系统会投递一条 user message（`<reminder>...</reminder>`）到同一个 Agent 的队列中，并在 Agent 空闲时自动触发一次 `run()`。
- 保证 `Agent.run()` 不会并发重入（避免多线程同时跑导致状态/持久化错乱）。
- 有基本防呆：非法 schedule、过小间隔、过多 reminder、异常情况下的日志与降级。
- 系统强制只有一个对话：维护单 active `conversationId`，并拒绝并行多会话/多连接造成的分叉。

---

## 非目标（Non-Goals）
- 不解决“离线/错过提醒”的语义（misfire 规则、补发/合并/丢弃策略）——写入 `TODO.md`。
- 不实现“影分身”（未来的多对话形态）：同一个 agent 的分身不会收到任何定时通知（v1 不做）——写入 `TODO.md`。
- 不提供 `list/get/update/pause/resume` 等更丰富 API（需要时再扩展或切换文件方案）。

---

## 关键约束与统一口径（v1 直接定死）
### 1) 时间语义：统一使用 UTC，规避 DST
- Reminder 的 “一次性触发时间” 一律用 UTC 表示（不接受本地时区输入）。
- 系统内部统一用 `datetime(timezone.utc)` 做比较与计算。

### 2) `month/year` 不是日历月/年，而是固定秒数间隔
- `1month = 30day`
- `1year = 365day`
- 这是“固定时长”，不考虑不同月份天数与 DST。

### 3) 工具只给 Agent 用，不为人类输入优化
- 不考虑兼容旧语法；直接以当前“最新语法”为准。

---

## 对外接口（Tools）
### 工具：`create_reminder`
签名（概念上）：
`create_reminder(reminder_name: str, reminder_content: str, opts: ReminderOpts)`

#### 参数约束
- `reminder_name`：全局唯一（v1 全局只有一个 agent/一个对话，所以“全局”即可）。
  - 建议：`1 <= len <= 64`，只允许 `[a-zA-Z0-9_-]`（避免路径/日志污染）。
- `reminder_content`：
  - 建议：`1 <= len <= 4000`（防止把超长内容塞回模型上下文导致自爆）。
- `opts`（二选一，使用 `kind` 区分）：
  1) **间隔触发（interval）**
     - `kind: "interval"`
     - `interval: str`
     - 语法：`{digit}{unit}(-{digit}{unit})*`
     - 分隔符：强制 `-`，不允许空格
     - 单位与顺序（强制、不可跳序、不可重复）：
       `year-month-day-hour-min-sec`
     - `digit`：十进制正整数（不允许 0）
     - 示例：
       - `5min`
       - `1hour-30min`
       - `1day-2hour-10min`
  2) **一次性触发（one_time）**
     - `kind: "one_time"`
     - `one_time_utc: str`
     - 格式：`YYYY-MM-DDTHH:mmZ`（精确到分钟）
     - 示例：`2026-03-30T01:23Z`

#### 返回值（建议）
`"OK"`

### 工具：`delete_reminder`
签名（概念上）：
`delete_reminder(reminder_name: str)`

返回值（建议）：
- 找到并删除：`"OK"`
- 不存在：抛出异常（例如 `ValueError("reminder 不存在: ...")`），框架会把异常以 tool result 形式回传给 Agent 自行纠正。

---

## Reminder 投递消息格式（唤醒 Agent）
Reminder 触发时，系统向 Agent 注入一条 **user message**，content 形如：

```
<reminder name="REMINDER_NAME">
REMINDER_CONTENT
</reminder>
```

说明：
- 必须保证闭合标签是 `</reminder>`（修复 draft 中缺失 `>` 的问题）。
- `name` 属性用于将来排障/展示；v1 模型侧不需要强依赖该字段。

---

## 抽象与模块划分（v1：以 WebSocketChatSession 为运行时）

### 1) `ReminderStore`（持久化）
职责：
- 保存所有 reminder 定义（name/content/schedule/next_fire_at 等）。
- 支持原子写入（参考 `ConversationStore._write_json_atomically()` 的做法）。

建议落盘位置：
- `SYSTEM_ROOT / "reminders" / "reminders.json"`
  - 复用 `backend/src/commons.py` 的 `SYSTEM_ROOT`。

建议数据模型（JSON 顶层）：
```
{
  "version": 1,
  "reminders": [
    {
      "name": "...",
      "content": "...",
      "schedule": { "kind": "interval", "interval_seconds": 5400 },
      "next_fire_at_utc": "2026-03-30T01:23:00Z",
      "created_at_utc": "..."
    }
  ]
}
```

### 2) `ReminderScheduler`（内存调度 + 到期投递）
职责：
- 启动时加载 `ReminderStore`（v1 当前实现会直接扫描 store，而不是维护最小堆）。
- 在会话运行期间持续运行（按当前实现：scheduler 跟随 `WebSocketChatSession` 启动）：
  - sleep-until-next-fire：每轮扫描并触发所有到期 reminder，并计算下一次最早触发时间。
  - wakeup：当 tool create/delete 修改了 store，会通过线程安全的 `notify_scheduler_changed()` 唤醒 scheduler 重新计算下一次触发时间。
  - 到期后触发投递：调用 WebSocketChatSession 的“内部投递函数”，把 `<reminder>...</reminder>` 当作一条普通 user message 注入队列，并确保 runner 被唤醒。
  - 若是 interval reminder：计算下一次 `next_fire_at`，写回 store，重新入堆。
  - 若是一次性 reminder：从 store 删除。

核心点：
- Scheduler **不直接调用** `Agent.run()`，只负责“注入 user message + 确保 runner 运行”。
- Scheduler 需要一个 `asyncio.Event`（wakeup event）：
  - tool handler 运行在 `Agent.run()` 的线程里（`asyncio.to_thread(...)`），因此必须用 `loop.call_soon_threadsafe(event.set)` 才能安全唤醒 event loop 线程上的 scheduler。

### 3) WebSocketChatSession 的最小改造点（避免引入新抽象）
目标：不额外引入 “AgentRuntime” 这种大抽象，而是在现有 WebSocketChatSession 上补齐 reminder 注入与并发保护。

改造点（v1 只做必要的）：
- 不引入“内部投递函数”：当前实现直接复用 `submit_user_message()` 来注入 reminder user message。
- 不引入 `run_lock`：scheduler 不直接调用 `Agent.run()`，只注入消息并确保 runner 存在，因此不会造成 `Agent.run()` 并发重入。

### 4) 工具实现：`ReminderTool`
职责：
- `create_reminder`：
  - 参数校验（pydantic）。
  - interval 解析 → `interval_seconds`
  - 写入 `ReminderStore`（若已存在同名则报错）
  - 调用 `notify_scheduler_changed()` 唤醒 scheduler 重新计算 next fire
  - 返回 `"OK"`
- `delete_reminder`：
  - 从 `ReminderStore` 删除
  - 调用 `notify_scheduler_changed()` 唤醒 scheduler 重新计算 next fire
  - 不存在则抛异常，让框架回传给 Agent

---

## 数据流（ASCII）
### 创建 reminder（Agent → Tool → Store/Scheduler）
```
Agent.run()
  └─ execute_tool_calls()
       └─ create_reminder(name, content, opts)
            ├─ ReminderStore.create_*(...)
            └─ notify_scheduler_changed()
```

### 触发 reminder（Scheduler → WebSocketChatSession → Agent.run）
```
ReminderScheduler 后台任务
  └─ 到期
       └─ await WebSocketChatSession.submit_user_message(...)
            └─ 确保 runner task 已启动
                 └─ await asyncio.to_thread(Agent.run)
```

---

## 防呆设计（v1 默认值）
（工具只给 Agent 用，但仍需要防止模型“自爆式”参数）
- `MIN_INTERVAL_SECONDS = 10`
- `MAX_ACTIVE_REMINDERS = 100`
- `MAX_NAME_LEN = 64`
- `MAX_CONTENT_LEN = 4000`
- 一次性时间 `one_time_utc` 允许“轻微过去”：
  - `one_time_utc < now_utc - 60s`：报错（避免疯狂立即触发）
  - `now_utc - 60s <= one_time_utc <= now_utc`：视为“立刻触发”

---

## 可观测性（必须打日志）
建议日志点（info/warn）：
- create/delete 成功与失败（包含 name、schedule_kind、next_fire_at）
- scheduler 装载了多少 reminders
- reminder 到期触发（name、next_fire_at、是否重排/删除）
- 投递失败/Agent 未初始化/会话未开始等异常路径

---

## 测试计划（pytest + uv）
建议新增最小单测覆盖：
- interval 解析：
  - 合法：`5min`、`1hour-30min`、`1year-2month-3day-4hour-5min-6sec`
  - 非法：重复单位、乱序、包含空格、包含 0、未知单位
- `month/year` 固定时长换算正确（30d/365d）
- `ReminderStore`：
  - 原子写入（写入后可读回、格式正确）
  - 唯一性约束（重复 name 报错）

（Scheduler 的“到期触发”建议通过可注入的 `now()` 函数/时钟来做可控测试；v1 也可以先不测 scheduler，只测 store+parser。）

---

## 与现有代码的集成点（实施时确认）
- `backend/src/tools/`：新增 `reminder.py` 暴露 `REMINDER_TOOL`（类似 `bash.py` / `reset_context.py`）。
- Agent 工具列表注入：
  - 当前在 `backend/src/websocket_chat_session.py:create_default_agent()` 里固定 `[BASH_TOOL, RESET_CONTEXT_TOOL]`；
  - v1 需要把 reminder 工具也挂上去（工具仅给 Agent 用）。
- `backend/src/web_app.py`：
  - v1 先不引入应用级生命周期管理：ReminderScheduler 由 WebSocketChatSession 初始化并常驻（按当前假设 WS 会常驻）。

---

## 单对话（单 active conversationId）实现策略（v1 必做）
### 1) active conversation 指针文件
- 路径：`SYSTEM_ROOT / "active_conversation_id.txt"`
- 写入时机：
  - 首次持久化（`on_conversation_persisted` 回调）时写入
  - `reset_context` 切换会话时更新写入（`on_reset_context` 回调中更新）
- 读取时机：
  - WebSocketChatSession 初始化时读取；
    - 若文件存在：强制 `resume_conversation(active_conversation_id)`（无视/校验前端传入的 `conversationId`，避免分叉）
    - 若文件不存在：说明系统还没有 active 会话（例如首次启动），才 `new_conversation()`

备注：
- 这里的判断依据是 **active_conversation_id 指针文件**，不是 reminder 文件。
- 当前实现里“new vs resume”原本由前端通过 `ws?conversationId=...` 决定；但既然 v1 要强制单对话，这个选择权需要收回到后端，以保证无论用户怎么点都只会落到同一个 active 会话上。

### 2) 防止多开 session
- v1 直接在 WebSocket 入口拒绝第二条连接（返回 error + close），保证不会出现“两个 session 各自跑自己的对话”的分叉。

---

## 需要写入 `TODO.md` 的事项（本计划不做）
- misfire/离线/错过语义（补发/合并/丢弃策略）
- 多对话（通过影分身）的实现与 reminder 归属策略（主本体收提醒，分身不收提醒）
- 更丰富的 reminder API（list/update/pause/resume）
- 是否需要把 reminder 投递事件显式投影到 UI/IM（目前只保证 Agent 被唤醒）
