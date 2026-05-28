import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import coolname

from src.commons import ORIGINALS_DIR

MEMORY_MANAGER_META_KEY = "memory-manager"
MEMORY_MANAGER_SUMMARY_AWAKEN_COUNT_KEY = "summary-awaken-count"
MEMORY_MANAGER_JUDGE_AWAKEN_COUNT_KEY = "judge-awaken-count"
MEMORY_MANAGER_LAST_TRIGGERED_THRESHOLD_KEY = "last-triggered-threshold"
PAUSE_META_KEY = "pause"
PAUSE_REQUESTED_KEY = "requested"
PAUSE_PAUSED_KEY = "paused"


def build_conversation_file_name() -> str:
    slug = coolname.generate_slug()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{slug}-{timestamp}.json"


def _parse_conversation_file_name_timestamp(conversation_file_name: str) -> str | None:
    stem = Path(conversation_file_name).stem
    _, separator, timestamp = stem.rpartition("-")
    if not separator:
        return None
    try:
        datetime.strptime(timestamp, "%Y%m%dT%H%M%S%fZ")
    except ValueError:
        return None
    return timestamp


def _strip_meta_for_runtime(message: dict[str, Any]) -> dict[str, Any]:
    runtime_message = dict(message)
    runtime_message.pop("meta", None)
    return runtime_message


def _validate_conversation_file_name(conversation_file_name: str) -> None:
    # conversation_file_name 是 conversation 文件名；这里做最小化的安全边界校验。
    if not conversation_file_name:
        raise ValueError("conversation_file_name 不能为空")
    if Path(conversation_file_name).name != conversation_file_name or conversation_file_name in {".", ".."}:
        raise ValueError("conversation_file_name 非法：不允许包含路径")
    if not conversation_file_name.lower().endswith(".json"):
        raise ValueError("conversation_file_name 非法：必须包含 .json 后缀")


class ConversationStore:
    def __init__(
        self,
        *,
        system_instruction: str,
        user_instruction: str,
        originals_dir: Path | None = None,
    ) -> None:
        """
        :param originals_dir: 主要是为了测试方便，所以这里把它作为一个参数。
        """
        self._system_instruction = system_instruction
        self._user_instruction = user_instruction
        self._originals_dir = (originals_dir or ORIGINALS_DIR).expanduser()
        self._file_path: Path | None = None
        self._conversation_file_name = ""
        self._messages: list[dict[str, Any]] = []
        self._memory_manager_summary_awaken_count = 0
        self._memory_manager_judge_awaken_count = 0
        self._memory_manager_last_triggered_threshold = 0
        self._pause_requested = False
        self._paused = False

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    @property
    def conversation_file_name(self) -> str:
        return self._conversation_file_name

    def has_persisted_conversation(self) -> bool:
        return self._file_path is not None

    @property
    def memory_manager_summary_awaken_count(self) -> int:
        return self._memory_manager_summary_awaken_count

    @property
    def memory_manager_judge_awaken_count(self) -> int:
        return self._memory_manager_judge_awaken_count

    @property
    def memory_manager_last_triggered_threshold(self) -> int:
        return self._memory_manager_last_triggered_threshold

    @property
    def pause_requested(self) -> bool:
        return self._pause_requested

    @property
    def paused(self) -> bool:
        return self._paused

    def update_memory_manager_state(self, *, summary_awaken_count: int, judge_awaken_count: int) -> None:
        if summary_awaken_count < 0 or judge_awaken_count < 0:
            raise ValueError("memory manager 状态不能为负数")
        self._memory_manager_summary_awaken_count = summary_awaken_count
        self._memory_manager_judge_awaken_count = judge_awaken_count
        if self.has_persisted_conversation():
            self._write_json_atomically()

    def update_memory_manager_last_triggered_threshold(self, *, last_triggered_threshold: int) -> None:
        if last_triggered_threshold < 0:
            raise ValueError("last_triggered_threshold 不能为负数")
        self._memory_manager_last_triggered_threshold = last_triggered_threshold
        if self.has_persisted_conversation():
            self._write_json_atomically()

    def update_pause_state(self, *, pause_requested: bool, paused: bool) -> None:
        self._pause_requested = bool(pause_requested)
        self._paused = bool(paused)
        if self.has_persisted_conversation():
            self._write_json_atomically()

    def start_with_first_user_message(self, *, user_content: str) -> None:
        if self.has_persisted_conversation():
            raise RuntimeError("conversation 已开始，不能重复创建")

        self._conversation_file_name = build_conversation_file_name()
        self._messages = [
            {"role": "system", "content": self._system_instruction},
            {"role": "user", "content": self._user_instruction},
            {"role": "user", "content": user_content},
        ]
        self._originals_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._originals_dir / self._conversation_file_name
        self._write_json_atomically()

    def start_with_messages(self, *, messages: list[dict[str, Any]]) -> None:
        """
        用于 reset-context 等场景：希望直接把一段已有对话片段落到新会话中。

        约束：
        - 不要求 messages 的第一条必须是 user；可以是 assistant/tool。
        - 调用方需自行保证 messages 不包含 system/user instruction（这里只会自动补前两条指令）。
        """
        if self.has_persisted_conversation():
            raise RuntimeError("conversation 已开始，不能重复创建")

        self._conversation_file_name = build_conversation_file_name()
        self._messages = [
            {"role": "system", "content": self._system_instruction},
            {"role": "user", "content": self._user_instruction},
            *[dict(m) for m in messages],
        ]
        self._originals_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._originals_dir / self._conversation_file_name
        self._write_json_atomically()

    @classmethod
    def load_from_conversation_file_name(
        cls,
        *,
        conversation_file_name: str,
        originals_dir: Path | None = None,
    ) -> "ConversationStore":
        _validate_conversation_file_name(conversation_file_name)
        resolved_originals_dir = (originals_dir or ORIGINALS_DIR).expanduser()
        if not resolved_originals_dir.exists():
            raise FileNotFoundError(f"originals 目录不存在: {resolved_originals_dir.as_posix()}")

        file_path = resolved_originals_dir / conversation_file_name
        if not file_path.exists():
            raise FileNotFoundError(f"conversation 不存在: {conversation_file_name}")

        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"conversation JSON 解析失败: {conversation_file_name}") from exc

        if not isinstance(payload, dict):
            raise ValueError("conversation JSON 顶层必须是 object")
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            raise ValueError("conversation JSON.meta 必须是 object")

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("conversation JSON.messages 必须是非空数组")
        if not all(isinstance(m, dict) for m in messages):
            raise ValueError("conversation JSON.messages 每个元素必须是 object")

        store = cls(system_instruction="", user_instruction="", originals_dir=resolved_originals_dir)
        store._file_path = file_path
        store._conversation_file_name = conversation_file_name
        store._messages = messages
        memory_manager_meta = meta.get(MEMORY_MANAGER_META_KEY)
        if isinstance(memory_manager_meta, dict):
            summary_awaken_count = memory_manager_meta.get(MEMORY_MANAGER_SUMMARY_AWAKEN_COUNT_KEY)
            judge_awaken_count = memory_manager_meta.get(MEMORY_MANAGER_JUDGE_AWAKEN_COUNT_KEY)
            last_triggered_threshold = memory_manager_meta.get(MEMORY_MANAGER_LAST_TRIGGERED_THRESHOLD_KEY)
            if isinstance(summary_awaken_count, int) and summary_awaken_count >= 0:
                store._memory_manager_summary_awaken_count = summary_awaken_count
            if isinstance(judge_awaken_count, int) and judge_awaken_count >= 0:
                store._memory_manager_judge_awaken_count = judge_awaken_count
            if isinstance(last_triggered_threshold, int) and last_triggered_threshold >= 0:
                store._memory_manager_last_triggered_threshold = last_triggered_threshold

        pause_meta = meta.get(PAUSE_META_KEY)
        if isinstance(pause_meta, dict):
            pause_requested = pause_meta.get(PAUSE_REQUESTED_KEY)
            paused = pause_meta.get(PAUSE_PAUSED_KEY)
            if isinstance(pause_requested, bool):
                store._pause_requested = pause_requested
            if isinstance(paused, bool):
                store._paused = paused

        # 继续旧对话时，system/user instruction 以历史为准（从 messages 的前两条恢复）。
        system_msg = messages[0]
        user_msg = messages[1] if len(messages) >= 2 else None
        if system_msg.get("role") == "system" and isinstance(system_msg.get("content"), str):
            store._system_instruction = system_msg["content"]
        if isinstance(user_msg, dict) and user_msg.get("role") == "user" and isinstance(user_msg.get("content"), str):
            store._user_instruction = user_msg["content"]

        return store

    @classmethod
    def find_latest_conversation_file_name(
        cls,
        *,
        originals_dir: Path | None = None,
    ) -> str | None:
        resolved_originals_dir = (originals_dir or ORIGINALS_DIR).expanduser()
        if not resolved_originals_dir.exists():
            return None

        latest: tuple[str, str] | None = None
        for path in resolved_originals_dir.glob("*.json"):
            if not path.is_file():
                continue
            timestamp = _parse_conversation_file_name_timestamp(path.name)
            if timestamp is None:
                continue

            candidate = (timestamp, path.name)
            if latest is None or candidate > latest:
                latest = candidate

        if latest is None:
            return None
        return latest[1]

    def append_message(self, message: dict[str, Any]) -> None:
        if not self.has_persisted_conversation():
            raise RuntimeError("conversation 尚未开始，不能追加消息")

        self._messages.append(message)
        self._write_json_atomically()

    def build_messages_from_history(self) -> list[dict[str, Any]]:
        """
        在resume conversation时使用
        :return:
        """
        return [_strip_meta_for_runtime(m) for m in self._messages]

    def _write_json_atomically(self) -> None:
        if self._file_path is None:
            raise RuntimeError("conversation 文件路径为空")

        payload = {
            "meta": {
                MEMORY_MANAGER_META_KEY: {
                    MEMORY_MANAGER_SUMMARY_AWAKEN_COUNT_KEY: self._memory_manager_summary_awaken_count,
                    MEMORY_MANAGER_JUDGE_AWAKEN_COUNT_KEY: self._memory_manager_judge_awaken_count,
                    MEMORY_MANAGER_LAST_TRIGGERED_THRESHOLD_KEY: self._memory_manager_last_triggered_threshold,
                },
                PAUSE_META_KEY: {
                    PAUSE_REQUESTED_KEY: self._pause_requested,
                    PAUSE_PAUSED_KEY: self._paused,
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
