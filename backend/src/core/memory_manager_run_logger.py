import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from typing import Literal

from src.commons import MEMORIES_ROOT


def _now_utc_iso_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class MemoryManagerRunLogger:
    conversation_file_name: str
    runner_kind: Literal["summarizer", "judge"]
    awaken_round: int

    def __post_init__(self) -> None:
        if self.runner_kind not in {"summarizer", "judge"}:
            raise ValueError("runner_kind 非法")
        if self.awaken_round <= 0:
            raise ValueError("awaken_round 必须为正数")

    def append_event(self, event: dict[str, Any]) -> None:
        logs_dir = MEMORIES_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        prefix = (self.conversation_file_name or "______")[:6]
        path = logs_dir / f"{prefix}{self.runner_kind}{self.awaken_round}.jsonl"
        payload = {"ts": _now_utc_iso_ms(), **event}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
