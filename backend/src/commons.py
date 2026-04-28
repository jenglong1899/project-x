import os
from pathlib import Path
from typing import Any

# todo。在shell工具里面做alias，这样模型可以少输入一些token

MEMORIES_ROOT = Path(os.getenv("PROJECT_X_MEMORIES_ROOT", "~/.project-x/memories"))
SUMMARIES_DIR = MEMORIES_ROOT / "summaries"

# 用MD而不是FILENAME，是因为容易和FILEPATH弄混
MAIN_MEMORY_MD = "MAIN_MEMORY.md"
TODO_MEMORY_MD = "TODO_MEMORY.md"

MAIN_MEMORY_FILEPATH = SUMMARIES_DIR / MAIN_MEMORY_MD
TODO_MEMORY_FILEPATH = MEMORIES_ROOT / TODO_MEMORY_MD

ORIGINALS_DIR = MEMORIES_ROOT / "originals"


def noop(*args: Any, **kwargs: Any) -> None:
    return None
