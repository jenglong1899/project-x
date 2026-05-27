import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from src.commons import BASE_ROOT


@dataclass(frozen=True)
class CodexTokens:
    access_token: str
    refresh_token: str


def _auth_root() -> Path:
    default_root = str(BASE_ROOT)
    return Path(os.getenv("PROJECT_X_AUTH_ROOT", default_root)).expanduser()


def _auth_path() -> Path:
    return _auth_root() / "auth.json"


@contextmanager
def _auth_store_lock() -> Iterator[None]:
    """
    约束：先做最小实现，只保证 Linux/macOS 下跨进程互斥写入。
    """
    import fcntl

    lock_path = _auth_root() / ".auth.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _load_auth_store() -> dict[str, Any]:
    path = _auth_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_auth_store(payload: dict[str, Any]) -> None:
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_codex_cli_tokens() -> CodexTokens | None:
    codex_home = os.getenv("PROJECT_X_CODEX_HOME", "").strip() or os.getenv("CODEX_HOME", "").strip()
    if not codex_home:
        codex_home = str(Path.home() / ".codex")
    path = (Path(codex_home).expanduser() / "auth.json")
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            return None
        access_token = str(tokens.get("access_token") or "").strip()
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if not access_token or not refresh_token:
            return None
        return CodexTokens(access_token=access_token, refresh_token=refresh_token)
    except Exception:
        return None


def resolve_codex_tokens(*, import_from_cli_if_missing: bool = True) -> CodexTokens:
    """
    优先读 project-x 自己的 auth store；缺失时可选从 ~/.codex/auth.json 导入一次。
    """
    with _auth_store_lock():
        store = _load_auth_store()
        state = store.get("openai-codex") if isinstance(store, dict) else None
        tokens = state.get("tokens") if isinstance(state, dict) else None
        access_token = str(tokens.get("access_token") or "").strip() if isinstance(tokens, dict) else ""
        refresh_token = str(tokens.get("refresh_token") or "").strip() if isinstance(tokens, dict) else ""
        if access_token and refresh_token:
            return CodexTokens(access_token=access_token, refresh_token=refresh_token)

        if not import_from_cli_if_missing:
            raise ValueError("缺少 Codex OAuth tokens（project-x auth.json 未配置）。")

        cli_tokens = _read_codex_cli_tokens()
        if cli_tokens is None:
            raise ValueError("缺少 Codex OAuth tokens（且未找到 ~/.codex/auth.json 可导入）。")

        store = {} if not isinstance(store, dict) else dict(store)
        store["openai-codex"] = {"tokens": {"access_token": cli_tokens.access_token, "refresh_token": cli_tokens.refresh_token}}
        _save_auth_store(store)
        return cli_tokens
