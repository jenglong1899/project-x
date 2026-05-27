import os
from pathlib import Path
from typing import Any, Literal

# todo。在shell工具里面做alias，这样模型可以少输入一些token

BASE_ROOT = Path(os.getenv("PROJECT_X_ROOT", "~/.project-x")).expanduser()
MEMORIES_ROOT = Path(os.getenv("PROJECT_X_MEMORIES_ROOT", str(BASE_ROOT / "memories"))).expanduser()
SUMMARIES_DIR = MEMORIES_ROOT / "summaries"

# 用MD而不是FILENAME，是因为容易和FILEPATH弄混
MEMORY_MAIN_MD = "MAIN.md"
MEMORY_TODO_MD = "TODO.md"

MAIN_MEMORY_FILEPATH = SUMMARIES_DIR / MEMORY_MAIN_MD
TODO_MEMORY_FILEPATH = SUMMARIES_DIR / MEMORY_TODO_MD

ORIGINALS_DIR = MEMORIES_ROOT / "originals"

WAKE_MEMORY_MANAGER_FLAG="<flag>WAKE_MEMORY_MANAGER_FLAG_POINT</flag>"
# RESET_CONTEXT_DECISION_MADE_FLAG="<flag>RESET_CONTEXT_DECISION_MADE_POINT</flag>"

ToolCallerKind = Literal["worker", "memory_manager_summary"]


def is_path_in_summaries_dir(*, path: Path) -> bool:
    resolved_path = path.resolve()
    resolved_summaries_dir = SUMMARIES_DIR.resolve()
    try:
        return resolved_path.is_relative_to(resolved_summaries_dir)
    except AttributeError:
        return resolved_summaries_dir == resolved_path or resolved_summaries_dir in resolved_path.parents


def assert_allowed_summaries_write(*, caller_kind: ToolCallerKind, target_path: Path) -> None:
    if not is_path_in_summaries_dir(path=target_path):
        return

    resolved_target = target_path.resolve()
    resolved_todo = TODO_MEMORY_FILEPATH.resolve()

    if caller_kind == "worker":
        if resolved_target != resolved_todo:
            raise ValueError(
                f"你是 worker：禁止编辑 {SUMMARIES_DIR.resolve()} 下除 {resolved_todo.name} 以外的文件；"
                f"当前目标是 {resolved_target}。"
                f"如果你要记录任务，请只编辑 {resolved_todo}。"
            )
        return

    if caller_kind == "memory_manager_summary":
        if resolved_target == resolved_todo:
            raise ValueError(
                f"你是 memory manager (summary)：禁止编辑 {resolved_todo.name}（它只允许 worker 编辑）。"
                f"请改为编辑 {MAIN_MEMORY_FILEPATH.resolve()} 或其他 summaries 下的摘要文件。"
            )
        return

    raise ValueError(f"无效的 caller_kind: {caller_kind!r}")


def noop(*args: Any, **kwargs: Any) -> None:
    return None
