# 记忆管理与 MagicWord reset-context 实施计划

## 背景与目标

当前系统把“用户可见会话”和“内部上下文分段”绑定得过紧：前端可以按 `conversationId` 切换历史会话，后端 `Agent` 也暴露 `new_conversation()` / `resume_conversation()`。新目标是让 worker 从用户视角更接近“一个持续工作的无限长对话”，内部仍然用多个 conversation JSON 文件承载上下文分段。

本阶段先实现记忆机制内核，不一次性重构前端产品形态：

- worker 不再通过 `reset_context` tool call 触发上下文重置，而是由系统检查最终 assistant message 中的 MagicWord。
- memory manager 由系统从 worker 当前上下文 fork 出来，专门维护记忆文件，并可通过 MagicWord 请求 reset-context。
- `build_memory_forked_subagent_prompt()` 需要补全 diff 能力，让 memory manager 看到“本轮上下文初始记忆”和“磁盘当前记忆”的差异。
- `reset_context` tool 进入废弃路径；模型可见工具应从默认 tools 中删除。
- 前端历史会话切换属于后续阶段：最终不再让用户按旧 conversation 文件继续聊天，conversation 文件只作为内部分段与审计记录。

## 本阶段边界

本阶段做：

- 实现 MagicWord 识别与内部 reset-context 编排。
- 实现 memory manager fork 的最小可运行流程。
- 实现记忆 diff。
- 保留现有 WebSocket / 前端协议的基本形状，避免把记忆内核和前端重构绑在一起。

本阶段不做，但要为后续留下明确方向：

- 不在本阶段删除前端侧栏和历史会话切换 UI。
- 不引入完整 thread 数据模型。
- 不迁移已有 conversation JSON 文件。
- 不改变 `ConversationStore` 的 JSON 主体格式，除非实现 MagicWord/reset 需要少量 meta。

## 核心抽象

### 1. `MemoryForkedSubagentRunner`

新增一个后端服务对象，负责“从 worker 当前上下文创建 memory manager，并让它处理记忆”。它不应该塞进 `ConversationStore`，因为它是运行时编排，不是存储模型。

职责：

- 接收 worker 当前 runtime messages 的快照。
- 基于 worker 当前上下文 fork 出一个 memory manager 上下文。
- 在 fork 后的上下文末尾 append `build_memory_forked_subagent_prompt(is_first_time_awaken=...)`，让同一个上下文里的 agent 切换到 memory manager 角色。
- 调用模型生成 memory manager 的最终 assistant message。
- 检查最终 assistant message 是否包含 reset MagicWord。
- 返回一个结构化结果给 `Agent`。

建议数据模型：

```python
@dataclass(frozen=True)
class MemoryManagerResult:
    requested_reset_context: bool
```

本阶段不需要把 memory manager 的完整对话存成 conversation JSON；如果要调试，可以先用 logger 记录关键事件。后续如果需要审计，再为 memory manager 增加单独 originals 目录。

### 2. MagicWord 检测

MagicWord 使用现有 prompt 中的 `PROJECT-X-RESET-CONTEXT`。

检测规则必须保守：

- 只检查一轮模型调用结束后的最终 assistant message，不在流式 delta 阶段触发。
- 只检查 assistant message 的文本 content。
- 只有当 content 中存在独立一行 `PROJECT-X-RESET-CONTEXT` 时才触发。
- 不需要剥离 MagicWord 后的内容；检测到后直接 reset-context。
- 检测逻辑保持简单，可以直接用一行 `content.splitlines()` 判断，不为它引入额外抽象。

### 3. `Agent` 内部 reset-context

`Agent` 仍然保留“内部创建新 conversation 分段”的能力，但不再把它暴露为 `new_conversation()` tool 语义。

本阶段建议先这样改：

- 把 `new_conversation()` 重命名为私有 `_start_new_conversation_segment()`。
- `Agent.__init__()` 内部直接调用 `_start_new_conversation_segment()`，调用者不再需要显式初始化。
- `resume_conversation()` 暂时保留给现有前端历史切换，标记为待废弃；后续移除前端历史切换后再删除。
- 内部 reset-context 由 `_start_new_context_with_auto_reminder()` 承担：
  - 重新 build system/user instruction。
  - 开启新的 conversation segment。
  - 用 auto reminder 作为新 segment 的第一条 user message。
  - 触发 `on_reset_context(conversation_id, display_name)`，让现有前端清空时间线并显示 auto reminder。

本阶段不再需要“第一次调用 reset_context 只返回 hint”的 tool 行为。是否重置由 memory manager 自己判断；当它输出 MagicWord，就代表系统应该执行 reset。

### 4. `Agent.run()` 中的记忆检查点

`Agent.run()` 不是每轮都运行 memory manager，而是每完成 20 个 worker 回合运行一次。

这里的 worker 回合指：worker 完成一次 assistant 生成，并把这次生成触发的普通工具调用链处理完。reset-context 后，回合计数清零。

主循环改成：

```text
用户消息入队
  ↓
模型生成 worker assistant message
  ↓
执行普通 tool_calls（如果有）
  ↓
当普通工具链结束时：
  worker 回合计数 +1
  ↓
如果计数未达到 20：
      结束本次运行
  ↓
如果计数达到 20：
  fork memory manager
  ↓
memory manager 更新记忆文件
  ↓
如果 memory manager 输出 reset MagicWord：
      Agent._start_new_context_with_auto_reminder()
      继续下一轮，让 auto reminder 驱动 worker 接着做
  否则：
      结束本次运行
```

注意点：

- memory manager 应该在 worker 一轮稳定结束后运行，避免 worker 正在等 tool result 时切上下文。
- memory manager 运行期间 worker 暂停，符合 prompt 中“worker 已暂停运行”的设定。
- 如果 memory manager 调用失败，不应该吞错；先让 WebSocket 返回 error，避免静默丢记忆。
- 如果 worker 自己输出 MagicWord，本阶段不触发 reset；触发权只给 memory manager，避免普通回答误触发。

### 5. 记忆 diff

`build_memory_forked_subagent_prompt(is_first_time_awaken=False)` 目前依赖 `_build_diff()`，需要实现。

需要保存两份内容：

- 当前 worker 上下文中加载过的 `MEMORY_MAIN_MD`（`MEMORY_MAIN.md`）内容。
- 磁盘中最新的 `MEMORY_MAIN_MD`（`MEMORY_MAIN.md`）内容。

建议在 `Agent` 初始化时保存一份 `loaded_main_memory_content`，传给 prompt builder 或 memory runner。这样 diff 不是从 prompt 字符串里反向解析，边界更清楚。

建议接口：

```python
def build_memory_forked_subagent_prompt(
    *,
    is_first_time_awaken: bool,
    loaded_main_memory_content: str,
) -> str:
    ...
```

`_build_diff()` 使用 `difflib.unified_diff()`，diff 两端分别是：

- `context-MEMORY_MAIN.md`
- `disk-MEMORY_MAIN.md`

如果 diff 为空，输出“无差异”，不要输出空 `<memory_diff>`。

## 文件级计划

### `backend/src/prompts/builder.py`

- 增加读取 main/todo memory 的小函数，避免 `build_user_level_instruction_zh()` 和 memory runner 重复读文件。
- 改造 `build_memory_forked_subagent_prompt()`：
  - 参数改为 keyword-only。
  - 接收 `loaded_main_memory_content`。
  - 实现 `_build_diff(loaded_main_memory_content, disk_main_memory_content)`。
- 保持现有中文 prompt 语义，只修正“输出 MagicWord 后系统重置上下文”的说明。

### `backend/src/core/agent.py`

- 初始化时内部创建 conversation segment。
- 把显式 `new_conversation()` 迁移到私有方法。
- 新增 memory manager runner 依赖，默认使用真实 runner，测试中可注入 fake runner。
- 维护 worker 回合计数，每完成 20 个 worker 回合后调用 memory manager。
- 依据 `MemoryManagerResult.requested_reset_context` 调内部 reset-context 流程。
- 暂时保留 `resume_conversation()`，但标记为前端历史切换删除后一起移除。

### `backend/src/core/agent_turn.py`

- 移除或旁路 `reset_context` tool 的特殊 directive 路径。
- 本阶段可以先保留普通 tool call 执行框架，避免影响 bash 等工具。
- `execute_tool_calls()` 只返回 tool messages，不返回 orchestration directive。

### `backend/src/tools/reset_context.py`

- 本阶段从 `create_default_agent()` 的 tools 列表中移除 `RESET_CONTEXT_TOOL`。
- 文件可暂时保留 auto reminder 常量，旧 `RESET_CONTEXT_TOOL` 定义应删除。
- `RESET_CONTEXT_AUTO_REMINDER` 如果仍被复用，可以移动到更合适的模块，例如 `core/memory_manager.py` 或 `core/reset_context.py`。

### `backend/src/websocket_chat_session.py`

- `create_default_agent()` 不再传入 `RESET_CONTEXT_TOOL`。
- 保留 `on_reset_context` 投影，继续发 `reset.context` 和 auto reminder 的 `user.message.committed`。
- 暂时保留 `conversationId` 参数兼容现有前端；后续阶段移除。

### 前端后续阶段

后续应删除历史会话切换：

- 去掉侧栏中的历史会话列表和选择逻辑。
- `chatClient.connect()` 不再接收 `conversationId`。
- `/ws?conversationId=...` 路径废弃。
- `/conversations` API 可保留为调试/审计接口，但不再是主 UI 功能。
- 用户看到的是一个当前 worker 工作流；内部 conversation segment 只用于上下文分段。

## 测试计划

### 单元测试

- MagicWord 检测：
  - 独立一行触发。
  - 普通句子中提到 `PROJECT-X-RESET-CONTEXT` 不触发。
  - 不剥离 content 中的 MagicWord；检测到后直接 reset-context。
- 记忆 diff：
  - 首次唤醒不输出 diff。
  - 非首次唤醒输出 unified diff。
  - 无差异输出“无差异”。
- `Agent`：
  - 初始化后无需调用 `new_conversation()` 即可 enqueue + run。
  - 未达到 20 个 worker 回合时，不调用 memory manager。
  - 达到 20 个 worker 回合且 memory manager 不请求 reset 时，本次运行正常结束。
  - memory manager 请求 reset 时，创建新的 conversation segment，触发 `on_reset_context`，并用 auto reminder 继续 worker。
  - `RESET_CONTEXT_TOOL` 不在默认 tools 中。

### 集成测试

- WebSocket：
  - 普通消息仍按 `user.message.committed → assistant delta → idle` 投影。
  - memory manager 触发 reset 时，事件顺序保持 `reset.context → user.message.committed(auto reminder) → assistant delta`。
- 回归测试：
  - bash tool 正常执行。
  - conversation JSON 仍能落盘，`lastChatTime` 正常更新。

### 手动验证

- 用 mock model 模拟 memory manager 输出 MagicWord，确认前端时间线清空并继续生成。
- 查看 `~/.project-x/memories/summaries/MEMORY_MAIN.md`，确认 memory manager 能写入摘要。
- 确认 worker 普通回答中不会展示 MagicWord。

## 风险与后续

- 本阶段保留前端历史切换，会和最终产品目标不一致；实施后要尽快做“前端单工作流化”阶段。
- memory manager 会额外调用模型，可能增加延迟；后续可加触发策略，例如每 N 轮或上下文增长到阈值后再 fork。
- 如果 memory manager 频繁误触发 reset，说明 prompt 或 MagicWord 检测太宽，应先收紧检测规则，而不是增加更多状态补丁。
- 长期应引入逻辑 thread，把多个 conversation segment 串起来；那时 `resume_conversation()` 应彻底删除，conversationId 只作为内部 segment id。
