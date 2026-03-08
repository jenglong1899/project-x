# 项目记忆

## 后端
- Python 项目在 `backend/`，使用 `uv` 管理依赖和运行命令。
- `src/core/model_config.py` 里的 `ModelConfig` 提供 `model`、`base_url`、`api_key`，供聊天模型调用使用。
- `src/core/chat.py` 的 `stream()` 负责发起流式模型请求，把 assistant 消息追加回 `messages`，并分别通过回调推送正文增量和思维链增量。
- `stream()` 需要兼容 `tool_calls` 的流式拼装，最终返回 OpenAI 风格的 assistant message dict。
- `stream()` 现在还支持三类工具调用流式事件：开始、参数增量、结束；`Agent` 会把这三类回调继续向外透传。
- `src/core/policies.py` 里有 DeepSeek 特殊规则：发送下一条 user message 前，要去掉上一轮 assistant message 的 `reasoning_content`。
- `src/core/chat.py` 和 `src/core/agent.py` 的回调接口用的是Protocol而不是Callable，因为这样可读性更高。
