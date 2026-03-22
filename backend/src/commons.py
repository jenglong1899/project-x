import os
from pathlib import Path

MEMORIES_ROOT = Path(os.getenv("BIONIC_CLAW_MEMORIES_ROOT", "~/.bionic-claw/memories"))
SUMMARIES_DIR = MEMORIES_ROOT / "summaries"
MAIN_MEMORY_PATH = SUMMARIES_DIR / "main.md"
ORIGINALS_DIR = MEMORIES_ROOT / "originals"
