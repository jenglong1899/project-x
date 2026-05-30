# 持久化 worker `bash` 工具的 CWD（思路 A）

## 目标与语义

- 目标：`bash`/`read_file` 共享的 `CwdState` **跨进程持久化**。
- 语义（全局最后 CWD）：关闭前后端后再打开，仍使用“上次最后一次 `bash` 执行后的 CWD”。
- reset-context：重置上下文时会重建 `Agent`/tools，但 **沿用重置前的 CWD**（因为它来自全局持久化状态，而不是新建时的默认 cwd）。
- 状态文件位置：放在 `BASE_ROOT`（`~/.project-x`，可被 `PROJECT_X_ROOT` 覆盖）。

## 现状（关键链路）

```
WS 连接建立
  -> create_default_agent()
      -> cwd_state = CwdState()            # 仅内存，来自 os.getcwd()
      -> tools = build_worker_tools(cwd_state=...)
          -> bash/read_file/replace/insert 共享 cwd_state

bash tool call
  -> BashTool.run(..., cwd=str(self._cwd_state.cwd))
  -> self._cwd_state.cwd = <state_file里读到的最终 PWD>
```

## 设计：一个“可持久化的 CwdState”

抽象：
- `CwdState` 仍是“当前 cwd”的内存对象（最小状态）。
- 增加一层“持久化协议”：
  - `load_persisted_cwd()`：从 `BASE_ROOT` 下的全局 state 文件读取 cwd（不存在/损坏则返回 `None`）。
  - `persist_cwd(cwd: Path)`：把 cwd 写回 state 文件（原子写：写临时文件后 `replace`）。

状态文件：
- 路径：`BASE_ROOT / "cwd_state.json"`（或同等简单名字；固定一个即可）。
- 内容：`{"cwd": "/abs/path"}`（只存一个字段，便于以后扩展）。
- 约束：只写入 `resolve()` 后的绝对路径字符串；读取后也 `expanduser().resolve()`。

写入时机（最小侵入）：
- 在 `backend/src/tools/bash.py` 中，当 `self._cwd_state.cwd = cwd` 更新后，**立刻持久化**一次。
  - 原因：`bash` 是“改变 cwd”最明确的来源；把“状态落盘”跟“状态更新”绑在一起，最不容易漏。
  - 结果：即使进程崩溃，只要最后一次 `bash` tool call 已返回，就能恢复到最新 cwd。

读取时机：
- 在 `create_default_agent()` 构造 `cwd_state` 时：
  - `persisted = load_persisted_cwd()`（None 则回退到 `CwdState()` 现有逻辑）。
  - `cwd_state = CwdState(initial_cwd=str(persisted))`。
- reset-context 后：因为 reset 会走同一条“新建 agent/tools”的路径，所以会自动读取持久化 cwd，从而沿用 reset 前的值。

并发/多窗口：
- 多个 WS 连接同时存在时，最后一次写入 wins（符合“全局最后 CWD”语义）。

## 需要改动的代码点（预计）

- `backend/src/commons.py`
  - 新增常量：`CWD_STATE_FILEPATH = BASE_ROOT / "cwd_state.json"`（确保放在 base root）。
- `backend/src/tools/cwd_state.py`
  - 增加 `load_persisted_cwd()` / `persist_cwd()`（读写 JSON，原子写）。
  - 可选：把“持久化路径”作为参数注入，便于测试；生产环境默认用 `CWD_STATE_FILEPATH`。
- `backend/src/tools/bash.py`
  - 在更新 `self._cwd_state.cwd` 后调用 `persist_cwd(...)`。
  - 注意：只对 `caller_kind="worker"` 生效（memory manager 已禁止调用 bash）。
- `backend/src/websocket_chat_session.py`
  - `create_default_agent()` 用 `load_persisted_cwd()` 初始化 `CwdState(initial_cwd=...)`。

## 测试思路（pytest，尽量小）

1) 单元测试：持久化读写协议
- 用 `tmp_path` 当作 `BASE_ROOT`：
  - `monkeypatch.setenv("PROJECT_X_ROOT", str(tmp_path))`
  - `importlib.reload(src.commons)` 以刷新 `BASE_ROOT/CWD_STATE_FILEPATH`
  - 调 `persist_cwd()` 写入，再调 `load_persisted_cwd()` 读回，断言一致。
- 覆盖损坏文件：写入非 JSON，`load_persisted_cwd()` 返回 `None`。

2) 轻量集成测试：`bash` 更新后会落盘
- 构造 `BashTool(caller_kind="worker", cwd_state=...)`，执行一个 `cd` 命令；
- 断言：
  - 返回后 `cwd_state.cwd` 已改变；
  - `cwd_state.json` 存在且内容为新 cwd。

运行命令：
- 在 `backend/` 下：`uv run pytest -q --tb=line`

## 风险与边界

- 如果用户手动删掉目录或 cwd 指向不存在路径：加载时发现不存在则回退到 `os.getcwd()`（避免启动即崩）。
- 后端进程启动时将 `os.getcwd()` 设置为默认工作目录（fresh install 为 `~/x-space`）：
  - prompt 里的 cwd 与工具初始 cwd 将保持一致；
  - 之后 `bash` 会基于持久化 state 更新工具 cwd（进程 cwd 不会随 tool call 改变）。
