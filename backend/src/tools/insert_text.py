import difflib
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import coolname
from pydantic import BaseModel, Field, model_validator

from src.core.agent_turn import Tool


class InsertTextInput(BaseModel):
    filepath: str = Field(min_length=1)
    needle: str = Field(min_length=1)
    direction: Literal["before", "after"]
    text: str | None = None
    text_from_file: Path | None = None

    @model_validator(mode="after")
    def _validate_text_source(self) -> "InsertTextInput":
        if (self.text is None) == (self.text_from_file is None):
            raise ValueError("text 与 text_from_file 必须且只能提供一个")
        return self


class InsertTextOutput(BaseModel):
    unified_diff: str


class InsertTextTool:
    def __init__(self, *, cwd_provider: Any) -> None:
        self._cwd_provider = cwd_provider

    def to_tool(self) -> Tool:
        return Tool(
            name="insert_text",
            description=(
                "在文件中把文本插入到 needle 的前面或后面（needle 必须唯一）。\n"
                "\n"
                "参数：\n"
                "- filepath：相对或绝对路径（相对路径按当前bash的cwd 解析）\n"
                "- needle：用于定位插入位置的字符串，必须在文件中唯一（不需要整行，只要唯一即可）\n"
                "- direction：'before' 或 'after'\n"
                "- text：要插入的文本\n"
                "- text_from_file：如果插入失败了，系统会自动保存 text 到一个文件中；下次可用该参数复用，避免重复粘贴。\n"
                "text 与 text_from_file 只能选一个\n"
                "\n"
                "返回：\n"
                "- unified_diff(hunks)\n"
            ),
            parameters_json_schema=InsertTextInput.model_json_schema(),
            handler=self.run,
        )

    async def run(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_input = InsertTextInput.model_validate(arguments)
        filepath = self._resolve_filepath(tool_input.filepath)
        text = self._resolve_text(tool_input)

        try:
            original_content = filepath.read_text(encoding="utf-8")
            updated_content = self._insert(
                content=original_content,
                needle=tool_input.needle,
                direction=tool_input.direction,
                text=text,
            )
            filepath.write_text(updated_content, encoding="utf-8")
        except Exception as exc:
            text_file = self._persist_text_to_tmp(text)
            raise ValueError(
                f"{exc}；已将 text 保存到 {text_file}，下次可用 text_from_file 复用"
            ) from exc

        unified_diff = self._build_unified_diff(
            before=original_content,
            after=updated_content,
        )
        return InsertTextOutput(unified_diff=unified_diff).model_dump()

    @staticmethod
    def _build_unified_diff(*, before: str, after: str) -> str:
        diff_lines = list(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile="a/file",
                tofile="b/file",
                n=3,
                lineterm="",
            )
        )
        if not diff_lines:
            return ""
        return "\n".join(diff_lines) + "\n"

    def _resolve_filepath(self, filepath: str) -> Path:
        path = Path(filepath).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self._cwd_provider.cwd / path).resolve()

    def _resolve_text(self, tool_input: InsertTextInput) -> str:
        if tool_input.text is not None:
            return tool_input.text

        text_path = tool_input.text_from_file
        if text_path is None:
            # 理论上不会发生：pydantic validator 已经确保 text 来源存在。
            raise ValueError("text 与 text_from_file 必须提供其一")

        resolved = self._resolve_path(text_path)
        return resolved.read_text(encoding="utf-8")

    def _resolve_path(self, path: Path) -> Path:
        expanded = path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return (self._cwd_provider.cwd / expanded).resolve()

    @staticmethod
    def _persist_text_to_tmp(text: str) -> Path:
        slug = coolname.generate_slug()
        suffix = uuid4().hex[:4]
        path = Path("/tmp") / f"text-{slug}-{suffix}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def _insert(
        *,
        content: str,
        needle: str,
        direction: Literal["before", "after"],
        text: str,
    ) -> str:
        first_index = content.find(needle)
        if first_index < 0:
            raise ValueError("未找到 needle")

        second_index = content.find(needle, first_index + len(needle))
        if second_index >= 0:
            raise ValueError("needle 不唯一：匹配到多处内容，请提供更具体的 needle")

        if direction == "before":
            insert_at = first_index
        elif direction == "after":
            insert_at = first_index + len(needle)
        else:
            raise ValueError(f"无效的 direction: {direction!r}，期望 'before' 或 'after'")

        return content[:insert_at] + text + content[insert_at:]


def create_insert_text_tool(*, cwd_provider: Any) -> Tool:
    return InsertTextTool(cwd_provider=cwd_provider).to_tool()
