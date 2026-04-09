import os
from pathlib import Path
from typing import Any

MEMORIES_ROOT = Path(os.getenv("PROJECT_X_MEMORIES_ROOT", "~/.project-x/memories"))
SUMMARIES_DIR = MEMORIES_ROOT / "summaries"
MAIN_MEMORY_PATH = SUMMARIES_DIR / "main.md"
ORIGINALS_DIR = MEMORIES_ROOT / "originals"


def noop(*args: Any, **kwargs: Any) -> None:
    return None
