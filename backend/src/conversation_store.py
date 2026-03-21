import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import coolname

from src.commons import ORIGINALS_DIR

DISPLAY_NAME_MAX_LENGTH = 10


def truncate_display_name(text: str, *, max_length: int = DISPLAY_NAME_MAX_LENGTH) -> str:
    return text[:max_length]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_conversation_filename() -> str:
    slug = coolname.generate_slug()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{slug}-{timestamp}.json"


class ConversationStore:
    def __init__(
        self,
        *,
        system_instruction: str,
        user_instruction: str,
        originals_dir: Path | None = None,
    ) -> None:
        self._system_instruction = system_instruction
        self._user_instruction = user_instruction
        self._originals_dir = (originals_dir or ORIGINALS_DIR).expanduser()
        self._file_path: Path | None = None
        self._display_name = ""
        self._messages: list[dict[str, Any]] = []

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    def has_persisted_conversation(self) -> bool:
        return self._file_path is not None

    def start_with_first_user_message(self, *, user_content: str) -> None:
        if self.has_persisted_conversation():
            raise RuntimeError("conversation 已开始，不能重复创建")

        self._display_name = truncate_display_name(user_content)
        self._messages = [
            self._with_meta_timestamp(
                {
                    "role": "system",
                    "content": self._system_instruction,
                }
            ),
            self._with_meta_timestamp(
                {
                    "role": "user",
                    "content": self._user_instruction,
                }
            ),
            self._with_meta_timestamp(
                {
                    "role": "user",
                    "content": user_content,
                }
            ),
        ]
        self._originals_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._originals_dir / build_conversation_filename()
        self._write_json_atomically()

    def append_message(self, message: dict[str, Any]) -> None:
        if not self.has_persisted_conversation():
            raise RuntimeError("conversation 尚未开始，不能追加消息")

        self._messages.append(self._with_meta_timestamp(message))
        self._write_json_atomically()

    def _with_meta_timestamp(self, message: dict[str, Any]) -> dict[str, Any]:
        stored_message = dict(message)
        stored_message["meta"] = {
            "timestamp": utc_now_iso(),
        }
        return stored_message

    def _write_json_atomically(self) -> None:
        if self._file_path is None:
            raise RuntimeError("conversation 文件路径为空")

        payload = {
            "meta": {
                "display-name": self._display_name,
            },
            "messages": self._messages,
        }
        temp_path = self._file_path.with_name(f".{self._file_path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self._file_path)
