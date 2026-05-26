import re
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import coolname
from pydantic import BaseModel, Field
from pydantic import model_validator

from src.commons import ToolCallerKind, assert_allowed_summaries_write
from src.core.agent_turn import Tool


class ReplaceTextInput(BaseModel):
    filepath: str = Field(min_length=1)
    mode: Literal["literal", "regex"]
    needle: str
    repl: str | None = None
    repl_from_file: Path | None = None
    allow_multiple_occurrences: bool = Field(default=False)

    @model_validator(mode="after")
    def _validate_repl_source(self) -> "ReplaceTextInput":
        if (self.repl is None) == (self.repl_from_file is None):
            raise ValueError("repl 与 repl_from_file 必须且只能提供一个")
        return self


class ReplaceTextTool:
    def __init__(self, *, cwd_provider: Any, caller_kind: ToolCallerKind) -> None:
        self._cwd_provider = cwd_provider
        self._caller_kind = caller_kind

    def to_tool(self) -> Tool:
        return Tool(
            name="replace_text",
            description=(
                "在文件中把匹配到的文本替换为新文本。\n"
                "\n"
                "非常重要：regex 模式可以在不完整粘贴大段文本的前提下替换很大范围内容。\n"
                "推荐使用形如 \"beginning.*?end\" 的表达式，让替换更快、更省 token。\n"
                "尤其是跨多行的大段内容，尽量用通配符避免逐字匹配。\n"
                "如果你关闭了 allow_multiple_occurrences，但表达式实际匹配到多处，本工具会返回错误，\n"
                "你可以据此收窄正则，或者显式允许多处替换。\n"
                "\n"
                "参数：\n"
                "- filepath：相对或绝对路径（相对路径按当前bash的cwd 解析）\n"
                "- mode：'literal' 或 'regex'\n"
                "- needle：查找内容；literal 模式为原样字符串；regex 模式为 Python re 语法（DOTALL + MULTILINE）\n"
                "- repl：替换内容；regex 模式支持用 $!1、$!2... 引用 needle 的捕获组\n"
                "- repl_from_file：如果编辑失败了，系统会自动保存repl到一个文件中，下次编辑时你用这个参数来引用之前的repl，这样你就不用重新再输入一遍repl了。\n"
                "repl和repl_from_file只能选一个\n"
                "- allow_multiple_occurrences：是否允许匹配并替换多处；否则匹配多次会返回错误\n"
                "\n"
                "返回：\n"
                "- ok（编辑成功）"
            ),
            parameters_json_schema=ReplaceTextInput.model_json_schema(),
            handler=self.run,
        )

    async def run(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_input = ReplaceTextInput.model_validate(arguments)
        filepath = self._resolve_filepath(tool_input.filepath)
        assert_allowed_summaries_write(caller_kind=self._caller_kind, target_path=filepath)
        repl = self._resolve_repl(tool_input)

        try:
            original_content = filepath.read_text(encoding="utf-8")
            updated_content, _replaced_count = self._replace(
                content=original_content,
                needle=tool_input.needle,
                repl=repl,
                mode=tool_input.mode,
                allow_multiple_occurrences=tool_input.allow_multiple_occurrences,
            )
            filepath.write_text(updated_content, encoding="utf-8")
        except Exception as exc:
            repl_file = self._persist_repl_to_tmp(repl)
            raise ValueError(
                f"{exc}；已将 repl 保存到 {repl_file}，下次可用 repl_from_file 复用"
            ) from exc

        return "ok"

    def _resolve_filepath(self, filepath: str) -> Path:
        path = Path(filepath).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self._cwd_provider.cwd / path).resolve()

    def _resolve_repl(self, tool_input: ReplaceTextInput) -> str:
        if tool_input.repl is not None:
            return tool_input.repl

        repl_path = tool_input.repl_from_file
        if repl_path is None:
            # 理论上不会发生：pydantic validator 已经确保 repl 来源存在。
            raise ValueError("repl 与 repl_from_file 必须提供其一")

        resolved = self._resolve_path(repl_path)
        return resolved.read_text(encoding="utf-8")

    def _resolve_path(self, path: Path) -> Path:
        expanded = path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return (self._cwd_provider.cwd / expanded).resolve()

    @staticmethod
    def _persist_repl_to_tmp(repl: str) -> Path:
        slug = coolname.generate_slug()
        suffix = uuid4().hex[:4]
        path = Path("/tmp") / f"repl-{slug}-{suffix}.txt"
        path.write_text(repl, encoding="utf-8")
        return path

    @staticmethod
    def _replace(
        *,
        content: str,
        needle: str,
        repl: str,
        mode: Literal["literal", "regex"],
        allow_multiple_occurrences: bool,
    ) -> tuple[str, int]:
        if mode == "literal":
            regex = re.escape(needle)
        elif mode == "regex":
            regex = needle
        else:
            raise ValueError(f"无效的 mode: {mode!r}，期望 'literal' 或 'regex'")

        flags = re.DOTALL | re.MULTILINE

        def validate_and_replace(match: re.Match) -> str:
            matched_text = match.group(0)

            # 多行匹配时，如果同一个表达式在已匹配的文本内部还能再次匹配，往往代表匹配范围过大且有歧义，
            # 例如：<start>...<start>...<end> 用 <start>.*?<end> 可能吞掉两段。
            if "\n" in matched_text and re.search(regex, matched_text[1:], flags=flags):
                raise ValueError(
                    "匹配存在歧义：搜索表达式在已匹配的文本内部还能再次匹配。请让正则更具体一些，避免吞掉不该替换的范围。"
                )

            def expand_backreference(m: re.Match) -> str:
                group_num = int(m.group(1))
                group_value = match.group(group_num)
                return group_value if group_value is not None else m.group(0)

            return re.sub(r"\$!(\d+)", expand_backreference, repl)

        updated_content, n = re.subn(regex, validate_and_replace, content, flags=flags)
        if n == 0:
            raise ValueError("未找到任何匹配项")
        if (not allow_multiple_occurrences) and n > 1:
            raise ValueError(f"匹配到 {n} 处内容；请让表达式更具体，或开启 allow_multiple_occurrences")
        return updated_content, n


def create_replace_text_tool(*, cwd_provider: Any, caller_kind: ToolCallerKind = "worker") -> Tool:
    return ReplaceTextTool(cwd_provider=cwd_provider, caller_kind=caller_kind).to_tool()
