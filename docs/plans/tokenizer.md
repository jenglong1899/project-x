## tokenizer 计划（draft）

目标：为 `deepseek-v4-flash` / `deepseek-v4-pro` / `qwen3.5-flash` / `qwen3.5-max` 提供**可复用、可测试**的 token 统计能力，用于：
- memory manager 的“上下文占比”提示（按 token）
- 未来做“接近窗口上限时的稳定性策略”（如提前 reset-context）

非目标：覆盖大量模型；做“通用 tokenizer 框架”。

### 核心思路
以 `transformers` tokenizer 的 **chat template(message packing)** 为准来算 prompt tokens（避免“只算 text”带来的系统性偏差）。

数据流（简化）：
```
messages(list[role, content])
    -> TokenizerRegistry 选中 tokenizer/chat_template/context_window
        -> apply_chat_template(tokenize=True) 得到 input_ids
            -> token_count
                -> percentage = token_count / context_window
```

### 抽象与接口（建议）
- `TokenCounter`（纯函数式接口即可，不做过度抽象）
  - `count_text_tokens(model: str, text: str) -> int`
  - `count_messages_tokens(model: str, messages: list[dict[str, Any]]) -> int`
  - `token_percentage(model: str, messages: list[dict[str, Any]]) -> int`
- `MODEL_SPECS: dict[str, ModelSpec]`
  - `context_window: int`（显式写死 4 个模型）
  - `tokenizer_id: str`（transformers 的 tokenizer 标识；允许后续改动）
  - `chat_template: str | None`（优先用 tokenizer 自带；缺失则用我们内置模板）

假设（需要接受）：
- 这 4 个模型在各自家族内 tokenizer 口径一致；若后续证伪，只需要拆分 `MODEL_SPECS` 即可。

### 实现要点（最少业务代码）
- tokenizer 加载做缓存（避免每次算 token 都 init 一遍）。
- `messages` 必须按我们实际在 runtime 里传给 LLM 的结构来算（否则就不叫“真实统计”）：
  - `role/content`（文本）
  - `reasoning_content`/`reasoning`（如果存在，按折叠进 content 的口径计入）
  - `name`（如果 upstream 会传，就必须计入；不允许静默忽略）
  - `tool_calls`（assistant 发起工具调用的结构化内容，必须按模板打包后计入）
  - `tool_call_id`（tool 消息关联字段，如果 upstream 会传，就必须计入）
  - 结论：**不做“忽略字段”的隐式降级**；遇到未知字段要 fail-fast，避免统计口径悄悄漂移。
- 若某模型 tokenizer 不支持 `apply_chat_template`：
  - 直接 fail-fast（抛异常）并给出“缺少 chat template”的明确错误；不要悄悄退化到“只算 text”。

### 兜底（仅用于“估算”，不承诺对齐计费）
当 tokenizer 初始化失败 / chat template 缺失 / 运行时异常时，允许提供一个**显式标注为 estimate** 的退化路径：
- `estimate_messages_tokens_by_chars(model, messages) -> int`
- 计算方式：把 messages 按同样的“打包规则”拼成纯文本（至少包含 role + content；如果有 tool_calls/tool_call_id/name 也拼进去），再用“字符/字节 → token”的启发式估算。
- 估算口径：`ceil(utf8_bytes / 4)`。
- 注意：这个值只能用于 UI 提示/防爆（例如“接近窗口上限”预警）；不用于需要严格对齐 usage 的逻辑。

### 测试思路
1) 单元测试（离线、稳定）
   - 对固定 `messages` 断言 token_count 为某个确定值（golden test）。
   - 覆盖：空 messages、长文本、包含多条 user/assistant 交替。

2) 集成测试（必须：真实 API 对齐）
   - 对同一份 `messages`：
     - 调用真实 LLM API（通过项目现有的 `litellm` 调用路径），拿 `usage.prompt_tokens`
     - 与本地 `count_messages_tokens` 做对比（期望完全一致；若发现系统性偏差，再回头调整 chat template/选择的 tokenizer）
   - 测试默认运行；如果缺少对应 provider 的 API key 环境变量，则 `pytest.skip()` 跳过该用例（不引入额外 gate）。
