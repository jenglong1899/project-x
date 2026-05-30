import json
from pathlib import Path

from src.tools.cwd_state import load_persisted_worker_cwd, persist_worker_cwd


def test_load_persisted_worker_cwd_falls_back_when_missing(tmp_path: Path) -> None:
    state_path = tmp_path / "cwd_state.json"
    cwd = load_persisted_worker_cwd(state_path=state_path)
    assert isinstance(cwd, Path)


def test_persist_and_load_roundtrip(tmp_path: Path) -> None:
    state_path = tmp_path / "cwd_state.json"
    persist_worker_cwd(cwd=tmp_path, state_path=state_path)
    assert load_persisted_worker_cwd(state_path=state_path) == tmp_path.resolve()

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["cwd"] == str(tmp_path.resolve())

