# 在 `SUMMARIES_DIR` 内限制写权限（worker vs summary）

## 目标

在记忆目录 `SUMMARIES_DIR`（`~/.project-x/memories/summaries/`）内建立**硬约束**，避免模型误写：

- `worker`：只能编辑 `SUMMARIES_DIR/TODO.md`；编辑其他文件直接报错（工具层报错，阻断写入）。
- `memory manager (summary)`：禁止编辑 `SUMMARIES_DIR/TODO.md`；若尝试编辑则直接报错。

> 备注：这里的“编辑”指会落盘写入的工具（当前主要是 `replace_text` / `insert_text`）。
> 同时要覆盖 `bash`：当 `bash` 命令疑似会写入到不允许的 summaries 文件时，同样返回报错（尽力而为的启发式拦截）。

## 核心抽象

### 1) 调用方身份（Tool Caller Kind）

定义一个轻量枚举/字面量，用于区分工具调用方：

- `worker`
- `memory_manager_summary`

### 2) 写入策略（Write Policy）

在“会写文件”的工具里统一做一次路径策略判断：

```
should_allow_write(caller_kind, target_path) -> (allowed: bool, reason: str)
```

判定规则只关心两点：

1. `target_path` 是否落在 `SUMMARIES_DIR` 内（含子路径）
2. 目标文件是否是 `SUMMARIES_DIR/TODO.md`

### 2.1) `bash` 启发式拦截（不是安全沙箱）

`bash` 是“任意执行”，我们做的是**防误操作**而非安全隔离：

- 通过字符串/正则做 preflight 检查，识别“命令里显式提到 summaries 路径 + 看起来是写操作”的情况并阻断。
- 仍然存在理论绕过（例如间接写入、脚本生成路径、二进制工具写文件等），这点要在代码/报错里明确告知模型。

我们的目标是：让模型“正常用法”下几乎不可能误写 summaries。

### 3) 统一的路径归一化

所有判断基于 `Path.resolve()` 后的绝对路径，避免 `../` 绕过：

```
resolved_target = resolved(cwd_provider.cwd / user_path)
resolved_summaries = SUMMARIES_DIR.resolve()
is_in_summaries = resolved_summaries in resolved_target.parents or resolved_target == resolved_summaries
```

## 实现思路（自顶向下）

### A. 在 `backend/src/commons.py` 增加“判定函数 + 报错文案”

- 新增调用方类型（建议用 `Literal[...]` 或 `Enum`）
- 新增两个函数：
  - `is_path_in_summaries_dir(path: Path) -> bool`
  - `assert_allowed_summaries_write(*, caller_kind, target_path: Path) -> None`
- 报错信息要明确告诉模型：
  - 你是谁（worker / summary）
  - 你要写哪个文件
  - 为什么不允许（规则）
  - 该怎么做（例如：worker 只允许写 TODO；summary 不允许写 TODO，需要写 MAIN.md 或其他）

### B. 给写文件工具加策略钩子

涉及工具：

- `backend/src/tools/replace_text.py`
- `backend/src/tools/insert_text.py`

改造方式：

- `ReplaceTextTool.__init__(..., caller_kind=...)`
- `InsertTextTool.__init__(..., caller_kind=...)`
- 在 `run()` 里解析出最终 `filepath`（已 resolve）后，写入前调用：
  - `assert_allowed_summaries_write(caller_kind=..., target_path=filepath)`

### C. 限制 `bash`（summary 硬禁止；worker 启发式拦截）

涉及工具：

- `backend/src/tools/bash.py`

改造方式：

- `create_bash_tool(..., caller_kind=...)`
- `BashTool.__init__(..., caller_kind=...)`
- **summary：只要调用 `bash` 就直接报错**（不执行命令）。这是一条硬规则，避免 summary 通过 bash 绕过 summaries 写入限制。
- worker：在真正执行 `bash -lc` 前做 preflight（启发式拦截）：
  - 若命令字符串显式包含 summaries 路径（例如 `~/.project-x/memories/summaries`、`/summaries/`、或 `SUMMARIES_DIR` 的 resolve 绝对路径）
  - 且命令疑似写操作（例如包含 `>`/`>>`/`tee`/`sed -i`/`perl -pi`/`python -c` 写入/`cat >`/`cp`/`mv`/`rm`/`vim`/`nano`/`truncate` 等关键模式）
  - 则按 worker 的 summaries 写规则拒绝：如果目标不是 `SUMMARIES_DIR/TODO.md`，拒绝

注意：

- 这里的“目标文件”只能做粗略推断（从命令里提取路径子串），做不到 100% 准确；因此它是“尽力而为”的防误操作。
- 报错信息要明确建议模型改用 `replace_text/insert_text` 来编辑 summaries。

### D. 工具声明保持一致（避免 provider 的 tools schema 缓存失效）

如果 worker 与 summary 的 tools 声明不同，会影响 provider 的工具缓存。

因此约束是：

- worker 与 summary **使用同一组 tool name/description/schema/顺序**
- 只在工具内部 handler 用 `caller_kind` 做权限分支

实现上可以这样做：

- `Agent` 仍然维护 `self._tools`（worker 侧创建）
- 在 `_maybe_wake_memory_manager()` 唤醒 summary runner 时，构造一套“summary tools 实例”，但它们的对外声明必须与 worker 完全一致（仅 handler 行为不同）

现状：

- Worker 的工具在 `backend/src/websocket_chat_session.py:create_default_agent()` 中创建并传给 `Agent`。
- Summary runner 目前直接复用 `self._tools`（与 worker 相同一套），无法区分身份。

改造：

- 在 `Agent` 内部（`backend/src/core/agent.py:_maybe_wake_memory_manager()`）调用 summary runner 时，不再传 `self._tools` 原样透传，而是传入一套“summary 专用 tools”（caller_kind=summary）。
- Worker 侧创建的工具保持 caller_kind=worker。

数据流（关键差异点）：

```
WS -> Agent(worker tools: caller=worker) -> tool(write) -> 允许写 summaries/TODO.md
                    |
                    +-> MemoryManagerSummaryRunner(tools: caller=summary)
                              -> tool(write) -> 禁止写 summaries/TODO.md
```

## 测试思路

新增单测覆盖“策略判定 + 工具阻断”两层：

1. `assert_allowed_summaries_write()`：
   - worker 写 `SUMMARIES_DIR/TODO.md`：允许
   - worker 写 `SUMMARIES_DIR/MAIN.md`：拒绝
   - summary 写 `SUMMARIES_DIR/TODO.md`：拒绝
   - summary 写 `SUMMARIES_DIR/MAIN.md`：允许
   - 非 summaries 目录：两者都允许（不影响原有行为）

2. 工具集成（尽量轻量）：
   - 构造临时目录作为 `MEMORIES_ROOT`（通过 env 或 monkeypatch `SUMMARIES_DIR` 的导入路径）
   - 对 `replace_text`/`insert_text`：
     - worker caller 改 TODO 成功
     - summary caller 改 TODO 报错（断言异常信息包含“summary 不允许编辑 TODO.md”）
   - 对 `bash`：
     - worker caller：尝试 `echo x > SUMMARIES_DIR/MAIN.md` 报错；`echo x >> SUMMARIES_DIR/TODO.md` 不报错（或至少不被此规则拦截）
     - summary caller：任意命令（例如 `pwd`）都报错（summary 禁止使用 bash）

运行：

- `cd backend && uv run pytest -q --tb=line`

## 关键假设/边界

- “编辑 summaries 文件”的入口目前只包括 `replace_text` / `insert_text`；若未来新增其他写工具（如 write_file），需要复用同一策略函数。
- 仅对 `SUMMARIES_DIR` 做限制；不改变其他目录的写行为。
