# 基于 usage 的上下文占用统计

## 目标

删掉 `backend/src/pkg/token_counter.py` 这套 tokenizer/估算逻辑。  
每轮模型调用结束后，直接读取 provider 返回的 `usage.prompt_tokens`，用它计算当前上下文占用百分比，继续驱动 memory manager 唤醒与 reset 后阈值初始化。

## 实现思路

先收敛“单轮模型调用结果”的抽象，避免 `Agent` 继续吃裸 assistant message：

```text
Agent.run
  -> agent_turn.stream(...)
      -> TurnResult
         |- assistant_message
         |- usage
  -> Agent 用 usage.prompt_tokens 算 used_percent
```

具体做法：

1. 在 `backend/src/core/agent_turn.py` 引入一个很小的返回结构（`dataclass` 即可），统一承载 `assistant_message` 和 `usage`。
2. `litellm` 路径从流式返回里提取最终 `usage`；`openai-codex` 路径从 Responses SSE 的完成事件里提取 `usage`；`mock` 路径返回空 usage。
3. 在 `backend/src/commons.py` 放模型上下文窗口映射与默认值；只保留“按模型名取窗口大小”的最小能力，不再保留 tokenizer 相关抽象。
4. `backend/src/core/agent.py` 改为依赖 `TurnResult.usage.prompt_tokens`：
   - `_maybe_wake_memory_manager()` 用本轮 `prompt_tokens` 算占比并更新阈值。
   - 如果 provider 没返回 `usage.prompt_tokens`，则退回到一个轻量本地估算（按消息 JSON 的 UTF-8 字节数 `/ 4`）。
   - `_reset_context()` 直接把阈值重置为 `0`，等下一轮真实 usage 或回退估算再重新建立阈值。

关键假设：

- 各 provider 返回的 `usage.prompt_tokens` 口径足够稳定，可直接当作“当前整个 prompt 上下文大小”；缺失时允许回退到轻量估算。
- memory manager 只需要在 assistant 轮结束后判断一次阈值，不需要在工具执行后额外重算。
- reset 后允许阈值暂时回到 `0`；这样比保留一套本地估算器更简单，也更符合“以真实 usage 为准”。

## 测试思路

1. `agent_turn` 单测：
   - 验证 `litellm`/`mock`/`codex` 路径都返回统一的 `TurnResult` 结构。
   - 验证有 usage 时能取到 `prompt_tokens`，缺失 usage 时也不会破坏调用链。

2. `agent` 单测：
   - 验证 `_maybe_wake_memory_manager()` 使用本轮 usage 触发阈值，而不是读 `TokenCounter`。
   - 验证 usage 缺失时会回退到本地估算，并继续正常推进阈值判断。
   - 验证 `reset_context()` 后阈值被清零，下一轮收到真实 usage 后再恢复正常唤醒。

3. 回归测试：
   - 跑与 memory manager、reset-context 相关的后端测试，确认对话持久化与唤醒次数未回归。
