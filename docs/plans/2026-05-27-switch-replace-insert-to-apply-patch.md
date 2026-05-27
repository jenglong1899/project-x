## 目标

当 `ModelConfig.provider == "openai-codex"` 时：
- worker 与 memory manager (summary) 不再暴露 `replace_text` / `insert_text`
- 改为暴露 `apply_patch`（基于 `codex-apply-patch`）

当 provider 不是 `openai-codex` 时：
- 维持现状（`replace_text` / `insert_text`），避免其它模型对 patch 格式不稳定导致可用性下降

## 关键约束 / 假设

- 假设 1：`apply_patch` 只允许修改“项目工作区内的相对路径”，拒绝绝对路径、`..` 等逃逸路径（避免误改用户机器上的任意文件）。
- 假设 2：沿用现有的 summaries 写入守卫：通过 `assert_allowed_summaries_write(caller_kind=..., target_path=...)` 约束 memory 文件编辑范围。
- 假设 3：依赖安装使用 `uv add codex-apply-patch`（它同时提供 Python API 与 `codex_apply_patch` CLI）。CLI 的转义问题通过 Python `subprocess.run(..., input=patch_text)` 规避。

如果你希望 `apply_patch` 允许绝对路径（例如直接改 `~/.project-x/...`），需要在实现前明确放开哪些目录白名单。

## 实现思路（自顶向下）

### 1) 新增 `apply_patch` 工具（后端 Tool）

新增文件：`backend/src/tools/apply_patch.py`

- 输入：`patch: str`
- 行为：
  1. 解析 patch，提取将被新增/修改/删除的文件路径列表
  2. 对每个目标路径做校验：
     - 必须是相对路径且不能包含 `..`
     - 解析为 `workspace_root / path` 后必须仍在 `workspace_root` 下
     - 调用 `assert_allowed_summaries_write(caller_kind=..., target_path=resolved_path)`
  3. 应用 patch：
     - 优先使用 `codex_apply_patch.apply_patch(patch_text)`（无 shell 转义问题）
     - （可选兜底）若 import 失败，再用 `subprocess.run(["codex_apply_patch"], input=patch_text, text=True, ...)`
  4. 返回结构化结果（added/modified/deleted/summary），用于前端 tool 卡片展示与 debug

### 2) 为不同 provider 构建不同工具集

改造 `backend/src/toolkits.py`：

- 把 `build_worker_tools(...)` / `build_memory_manager_summary_tools()` 变成可根据 provider 切换：
  - codex：`bash + read_file + apply_patch`
  - 非 codex：`bash + read_file + replace_text + insert_text`

实现方式：
- `build_worker_tools(*, cwd_state: CwdState, provider: str) -> list[Tool]`
- `build_memory_manager_summary_tools(*, provider: str) -> list[Tool]`

然后在创建 Agent 时把 provider 传进去：
- `backend/src/websocket_chat_session.py`：`build_worker_tools(..., provider=model_config.provider)`
- `backend/src/core/agent.py`：`_build_memory_manager_summary_tools()` 改为使用 `self._model_config.provider`

### 3)（可选）提示模型 patch 格式

默认不新增专门的 system/user instruction。

原因：Codex / GPT 系列对 `*** Begin Patch` 这套格式通常已经足够熟悉；而且我们会把 patch 格式与路径约束写进 `apply_patch` 工具的 `description` 里（这比“额外的长指令”更直接，也更不容易污染其它行为）。

如果上线后观察到：
- patch 格式经常写错（缺 `*** Begin Patch` / header 行等）
- 或者经常尝试写绝对路径 / 越界路径

再考虑只在 `openai-codex` provider 下追加一小段补充指令即可（不需要引用整份 `get_tool_instructions()`）。

## 测试思路

1) 单测（优先）
- `apply_patch` 工具的路径校验：
  - 拒绝绝对路径、`../`、越界路径
  - worker 只能写 `summaries/TODO.md`
  - memory_manager_summary 不能写 `summaries/TODO.md`
- patch 应用的基本用例：
  - Add/Update/Delete 一个小文件

2) 集成测试（可选）
- 用 `PROJECT_X_MODEL_CONFIG=mock` 跑一轮，确认非 codex provider 仍然走 replace/insert
- 用 `PROJECT_X_MODEL_CONFIG=openai-codex`（本地有 OAuth 凭据时）验证工具列表切换与 patch 可用性
