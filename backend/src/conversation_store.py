import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import coolname

from src.commons import ORIGINALS_DIR

DISPLAY_NAME_MAX_LENGTH = 20
DISPLAY_NAME_ELLIPSIS = "..."
MEMORY_MANAGER_META_KEY = "memory-manager"
MEMORY_MANAGER_TURNS_KEY = "turns-since-memory-manager"
MEMORY_MANAGER_AWAKEN_COUNT_KEY = "awaken-count"


def truncate_display_name(text: str, *, max_length: int = DISPLAY_NAME_MAX_LENGTH) -> str:
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}{DISPLAY_NAME_ELLIPSIS}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_conversation_id() -> str:
    slug = coolname.generate_slug()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{slug}-{timestamp}.json"


def _strip_meta_for_runtime(message: dict[str, Any]) -> dict[str, Any]:
    runtime_message = dict(message)
    runtime_message.pop("meta", None)
    return runtime_message


def _validate_conversation_id(conversation_id: str) -> None:
    # conversation_id 是 conversation 文件名；这里做最小化的安全边界校验。
    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    if Path(conversation_id).name != conversation_id or conversation_id in {".", ".."}:
        raise ValueError("conversation_id 非法：不允许包含路径")
    if not conversation_id.lower().endswith(".json"):
        raise ValueError("conversation_id 非法：必须包含 .json 后缀")


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
        self._conversation_id = ""
        self._messages: list[dict[str, Any]] = []
        self._memory_manager_turns_since_memory_manager = 0
        self._memory_manager_awaken_count = 0

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def last_chat_time(self) -> str:
        """
        会话列表的“最后活跃时间”。

        用途：
        - 前端侧栏按“最近聊天”排序
        - （可选）前端展示“上次聊天时间”

        取值规则：取最后一条持久化消息的 meta.timestamp（UTC ISO 字符串）。
        """
        for message in reversed(self._messages):
            meta = message.get("meta")
            if not isinstance(meta, dict):
                continue
            timestamp = meta.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                return timestamp
        return ""

    def has_persisted_conversation(self) -> bool:
        return self._file_path is not None

    @property
    def memory_manager_turns_since_memory_manager(self) -> int:
        return self._memory_manager_turns_since_memory_manager

    @property
    def memory_manager_awaken_count(self) -> int:
        return self._memory_manager_awaken_count

    def update_memory_manager_state(
        self,
        *,
        turns_since_memory_manager: int,
        awaken_count: int,
    ) -> None:
        if turns_since_memory_manager < 0 or awaken_count < 0:
            raise ValueError("memory manager 状态不能为负数")
        self._memory_manager_turns_since_memory_manager = turns_since_memory_manager
        self._memory_manager_awaken_count = awaken_count
        if self.has_persisted_conversation():
            self._write_json_atomically()

    def start_with_first_user_message(self, *, user_content: str, display_name: str | None = None) -> None:
        if self.has_persisted_conversation():
            raise RuntimeError("conversation 已开始，不能重复创建")

        self._conversation_id = build_conversation_id()
        resolved_display_name = display_name if display_name is not None else user_content
        self._display_name = truncate_display_name(resolved_display_name)
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
        self._file_path = self._originals_dir / self._conversation_id
        self._write_json_atomically()

    @classmethod
    def load_from_conversation_id(
        cls,
        *,
        conversation_id: str,
        originals_dir: Path | None = None,
    ) -> "ConversationStore":
        _validate_conversation_id(conversation_id)
        resolved_originals_dir = (originals_dir or ORIGINALS_DIR).expanduser()
        if not resolved_originals_dir.exists():
            raise FileNotFoundError(f"originals 目录不存在: {resolved_originals_dir.as_posix()}")

        file_path = resolved_originals_dir / conversation_id
        if not file_path.exists():
            raise FileNotFoundError(f"conversation 不存在: {conversation_id}")

        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"conversation JSON 解析失败: {conversation_id}") from exc

        if not isinstance(payload, dict):
            raise ValueError("conversation JSON 顶层必须是 object")
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            raise ValueError("conversation JSON.meta 必须是 object")
        display_name = meta.get("display-name", "")
        if not isinstance(display_name, str):
            raise ValueError("conversation JSON.meta.display-name 必须是 string")

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("conversation JSON.messages 必须是非空数组")
        if not all(isinstance(m, dict) for m in messages):
            raise ValueError("conversation JSON.messages 每个元素必须是 object")

        store = cls(system_instruction="", user_instruction="", originals_dir=resolved_originals_dir)
        store._file_path = file_path
        store._display_name = display_name
        store._conversation_id = conversation_id
        store._messages = messages
        memory_manager_meta = meta.get(MEMORY_MANAGER_META_KEY)
        if isinstance(memory_manager_meta, dict):
            turns_since_memory_manager = memory_manager_meta.get(MEMORY_MANAGER_TURNS_KEY)
            awaken_count = memory_manager_meta.get(MEMORY_MANAGER_AWAKEN_COUNT_KEY)
            if isinstance(turns_since_memory_manager, int) and turns_since_memory_manager >= 0:
                store._memory_manager_turns_since_memory_manager = turns_since_memory_manager
            if isinstance(awaken_count, int) and awaken_count >= 0:
                store._memory_manager_awaken_count = awaken_count

        # 继续旧对话时，system/user instruction 以历史为准（从 messages 的前两条恢复）。
        system_msg = messages[0]
        user_msg = messages[1] if len(messages) >= 2 else None
        if system_msg.get("role") == "system" and isinstance(system_msg.get("content"), str):
            store._system_instruction = system_msg["content"]
        if isinstance(user_msg, dict) and user_msg.get("role") == "user" and isinstance(user_msg.get("content"), str):
            store._user_instruction = user_msg["content"]

        return store

    def append_message(self, message: dict[str, Any]) -> None:
        if not self.has_persisted_conversation():
            raise RuntimeError("conversation 尚未开始，不能追加消息")

        self._messages.append(self._with_meta_timestamp(message))
        self._write_json_atomically()

    def build_messages_from_history(self) -> list[dict[str, Any]]:
        """
        在resume conversation时使用
        :return:
        """
        return [_strip_meta_for_runtime(m) for m in self._messages]

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
                MEMORY_MANAGER_META_KEY: {
                    MEMORY_MANAGER_TURNS_KEY: self._memory_manager_turns_since_memory_manager,
                    MEMORY_MANAGER_AWAKEN_COUNT_KEY: self._memory_manager_awaken_count,
                },
            },
            "messages": self._messages,
        }
        temp_path = self._file_path.with_name(f".{self._file_path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self._file_path)
