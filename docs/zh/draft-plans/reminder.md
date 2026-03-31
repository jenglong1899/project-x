create_reminder(reminder_name: str, reminder_content: str, opts: ReminderOpts)

定位：这个 reminder 是给 agent 用的，用来实现“主动性”（例如每隔 30 分钟检查一次某件事，然后决定要不要采取行动）。用户能看到 reminder 的触发消息，这是预期行为。

约束：
- reminder_name 必须唯一（建议至少在同一会话/同一 conversation 内唯一）。
- 最小间隔：10sec（小于则拒绝创建）。
- 全局上限：1000 个 reminder（超过则拒绝创建）。

两种触发方式：
1) 固定间隔（interval）：每隔固定时间触发一次。
   - 输入格式：用 `-` 分段的 duration（更容易解析），每段都是 `{digit}{unit}`。
   - digit：正整数；unit：Literal["sec", "min", "hour", "day"]（先不支持 month/year，避免日历语义争议）。
   - 示例：
     - `5min`：每隔 5 分钟触发一次
     - `1hour-30min`：每隔 1 小时 30 分钟触发一次
   - 解析规则建议：
     - 不允许重复 unit（例如 `1min-30min`）
     - 解析后归一成总秒数，并校验 >= 10sec

2) 只触发一次（once）：到指定时间触发一次。
   - 输入格式建议：ISO 8601（必须带时区 offset），例如 `2026-04-01T10:30:00+08:00`
   - 备注：避免“年月日小时分”这种不含时区、难校验的格式。

delete_reminder(reminder_name:str)

条件触发后，系统自动发送一条消息：
```
<reminder>
reminder_content
</reminder>
```
实现建议：复用现有的 enqueue（通过 WebSocketChatSession 注入一条 user message），并复用 WebSocketChatSession 对 agent.run() 重入的保护逻辑。
注意：需要明确 reminder 触发是否会驱动 agent.run() 继续生成/执行工具（用于实现主动性），以及断线/重启时 reminder 是否暂停（先按“会话存活时触发”实现，最简单）。


另一种方案，不提供工具，而是用文字描述这些规范，然后ai编辑一个文件就可以操作定时任务(~/.project-x/reminder.yml)
那应该就是需要系统监控这个文件的变动？
暂时不考虑这个方案，会引入一些未知的东西，之前没试过做这种。
