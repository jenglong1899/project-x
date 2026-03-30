import os
from pathlib import Path

_SYSTEM_ROOT_ENV = os.getenv("PROJECT_X_SYSTEM_ROOT")
_MEMORIES_ROOT_ENV = os.getenv("PROJECT_X_MEMORIES_ROOT")

# 说明：
# - v1 需要一个稳定的 SYSTEM_ROOT，用于放置 active_conversation_id、reminders 等“系统级”文件。
# - 为了便于本地开发与测试，支持通过环境变量覆盖路径。
# - 若只配置了 PROJECT_X_MEMORIES_ROOT（历史约定），则默认 SYSTEM_ROOT=MEMORIES_ROOT 的父目录，
#   这样 reminders 也会落在同一个临时目录树中，避免写到真实的 ~/.project-x。
if _SYSTEM_ROOT_ENV:
    SYSTEM_ROOT = Path(_SYSTEM_ROOT_ENV)
elif _MEMORIES_ROOT_ENV:
    SYSTEM_ROOT = Path(_MEMORIES_ROOT_ENV).expanduser().parent
else:
    SYSTEM_ROOT = Path("~/.project-x")

if _MEMORIES_ROOT_ENV:
    MEMORIES_ROOT = Path(_MEMORIES_ROOT_ENV)
else:
    MEMORIES_ROOT = SYSTEM_ROOT / "memories"

SYSTEM_ROOT = SYSTEM_ROOT.expanduser()
MEMORIES_ROOT = MEMORIES_ROOT.expanduser()

SUMMARIES_DIR = MEMORIES_ROOT / "summaries"
MAIN_MEMORY_PATH = SUMMARIES_DIR / "main.md"
ORIGINALS_DIR = MEMORIES_ROOT / "originals"

REMINDERS_DIR = SYSTEM_ROOT / "reminders"
REMINDERS_PATH = REMINDERS_DIR / "reminders.json"

ACTIVE_CONVERSATION_ID_PATH = SYSTEM_ROOT / "active_conversation_id.txt"
