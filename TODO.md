# TODO（Project X）

> 这里记录“已明确将来要做，但 v1 暂不做”的事项，避免遗忘与反复讨论。

## Reminder（定时唤醒 / 主动性）
- 定义并实现“离线/错过提醒”的语义（misfire：补发/合并/丢弃/按时间线插入策略）
- 影分身（未来的多对话形态）：同一 agent 的分身不接收任何定时通知；reminder 只路由到主本体
- 扩展 API：`list/get/update/pause/resume`
- 可观测性增强：指标/追踪（例如触发次数、延迟、失败率）

## Agent 的接口
- 现在防止 run 重入是在 `WebSocketChatSession` 里面做的，这种能力是否应该下沉到 `Agent`？
- 你提到的想法：删掉 `enqueue_user_msg` 和 `run`，提供 `enqueue_user_msg_and_ensure_running`

# Agent的接口
现在防止run重入是在 websocketChatSession里面做的，这种功能是不是应该放在Agent里面？
删掉enqueue_user_msg和run，提供enqueue_user_msg_and_ensure_running
