import os
from pathlib import Path

# 注意：这里必须在 import 项目代码之前生效。
# 否则 `src/commons.py` 等模块在 import 阶段就会把默认路径缓存到 `~/.project-x`，
# 在 Codex CLI 沙盒里会因为只读而导致测试失败。
_ROOT = Path("/tmp/project-x-memories-pytest")
_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PROJECT_X_MEMORIES_ROOT"] = str(_ROOT)
