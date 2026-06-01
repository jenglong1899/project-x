import os
from pathlib import Path
import json
import tempfile
import logging

from src.commons import CWD_STATE_FILEPATH, DEFAULT_WORKER_CWD


logger = logging.getLogger(__name__)


class CwdState:
    def __init__(self, *, initial_cwd: str | None = None) -> None:
        self.cwd = Path(initial_cwd or os.getcwd()).expanduser().resolve()


def load_persisted_worker_cwd(*, state_path: Path = CWD_STATE_FILEPATH) -> Path:
    if not state_path.exists():
        return DEFAULT_WORKER_CWD

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning(
            "worker cwd state 文件不可用，回退默认 cwd（path=%s error=%s: %s）",
            state_path,
            type(exc).__name__,
            exc,
        )
        return DEFAULT_WORKER_CWD

    cwd_value = payload.get("cwd")
    if not isinstance(cwd_value, str) or not cwd_value.strip():
        return DEFAULT_WORKER_CWD

    candidate = Path(cwd_value).expanduser().resolve()
    if not candidate.exists():
        return DEFAULT_WORKER_CWD
    return candidate


def persist_worker_cwd(*, cwd: Path, state_path: Path = CWD_STATE_FILEPATH) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cwd": str(cwd.expanduser().resolve())}

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(state_path.parent),
        prefix=f".{state_path.name}.",
        suffix=".tmp",
    ) as f:
        f.write(json.dumps(payload, ensure_ascii=False))
        tmp_path = Path(f.name)

    tmp_path.replace(state_path)
