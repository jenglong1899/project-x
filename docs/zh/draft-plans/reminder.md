create_reminder(reminder_name:str, reminder_content:str, opts:ReminderOpts)
reminder_name必须是唯一的。
两种选择，一个是每隔固定时间提醒一次，一个是只提醒一次。
- 每隔固定时间提醒一次，它的输入是 `{digit}{unit}`。digit是int类型，unit = Literal[ 'sec', 'min', 'hour', 'day', 'month', 'year' ]，如果输入是`5min`，那就是创建一个每隔5分钟提醒一次的reminder，如果输入是`1hour30min`,就是一个半小时提醒一次。
- 只提醒一次，输入是年月日小时分。

delete_reminder(reminder_name:str)

条件触发后，系统自动发送一条消息：
```
<reminder>
reminder_content
</reminder
```
如果不在run，就run


另一种方案，不提供工具，而是用文字描述这些规范，然后ai编辑一个文件就可以操作定时任务(~/.project-x/reminder.yml)
那应该就是需要系统监控这个文件的变动？
暂时不考虑这个方案，会引入一些未知的东西，之前没试过做这种。
