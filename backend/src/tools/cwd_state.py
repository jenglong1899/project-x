import os
from pathlib import Path


class CwdState:
    def __init__(self, *, initial_cwd: str | None = None) -> None:
        self.cwd = Path(initial_cwd or os.getcwd()).expanduser().resolve()
