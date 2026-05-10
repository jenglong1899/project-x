import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.core.agent_turn import Tool


DEFAULT_MAX_CHARS = 30_000


class ReadFileInput(BaseModel):
    filepath: str = Field(min_length=1, description="要读取的文件路径，支持相对路径或绝对路径")
    line_range_start: int = Field(default=1, ge=1, description="起始行号，最小值为 1")
    line_range_end: int | None = Field(default=None, description="结束行号，闭区间；不传则读到文件末尾或 max_chars 上限")
    show_line_numbers: bool = Field(default=True, description="是否显示行号")
    max_chars: int = Field(default=DEFAULT_MAX_CHARS, gt=0, description="最大输出字符数。超过限制则按完整行截断输出。")

    @model_validator(mode="after")
    def validate_line_range(self) -> "ReadFileInput":
        if self.line_range_end is not None and self.line_range_end < self.line_range_start:
            raise ValueError("line_range_end 必须大于等于 line_range_start")
        return self


class ReadFileOutput(BaseModel):
    filepath: str
    start: int
    # end 表示实际返回内容的最后一行；没有返回任何行时为 None，调用方不要把它理解成请求范围的结束。
    end: int | None
    content: str
    truncated: bool
    error: str | None = None


class ReadFileTool:
    def __init__(self, *, cwd_provider: Any) -> None:
        self._cwd_provider = cwd_provider

    def to_tool(self) -> Tool:
        return Tool(
            name="read_file",
            description="输出格式类似 nl -ba <filepath> | sed -n '<number,<number>p'，即行号和内容之间会有一个制表符，且行号是右对齐",
            parameters_json_schema=ReadFileInput.model_json_schema(),
            handler=self.run,
        )

    async def run(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_input = ReadFileInput.model_validate(arguments)
        filepath = self._resolve_filepath(tool_input.filepath)
        lines = self._read_selected_lines(filepath=filepath, tool_input=tool_input)
        content, end, truncated = self._format_content(lines=lines, tool_input=tool_input)
        return ReadFileOutput(
            filepath=str(filepath),
            start=tool_input.line_range_start,
            end=end,
            content=content,
            truncated=truncated,
        ).model_dump()

    def _resolve_filepath(self, filepath: str) -> Path:
        path = Path(filepath).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self._cwd_provider.cwd / path).resolve()

    @staticmethod
    def _read_selected_lines(*, filepath: Path, tool_input: ReadFileInput) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        with filepath.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if line_number < tool_input.line_range_start:
                    continue
                if tool_input.line_range_end is not None and line_number > tool_input.line_range_end:
                    break
                result.append((line_number, line))
        return result

    def _format_content(
        self,
        *,
        lines: list[tuple[int, str]],
        tool_input: ReadFileInput,
    ) -> tuple[str, int | None, bool]:
        chunks: list[str] = []
        current_size = 0
        end: int | None = None
        for line_number, line in lines:
            formatted_line = self._format_line(
                line_number=line_number,
                line=line,
                show_line_numbers=tool_input.show_line_numbers,
            )
            if current_size + len(formatted_line) > tool_input.max_chars:
                return "".join(chunks), end, True
            chunks.append(formatted_line)
            current_size += len(formatted_line)
            end = line_number
        return "".join(chunks), end, False

    @staticmethod
    def _format_line(*, line_number: int, line: str, show_line_numbers: bool) -> str:
        if not show_line_numbers:
            return line
        # :6表示右对齐
        return f"{line_number:6}\t{line}"

def create_read_file_tool(*, cwd_provider: Any) -> Tool:
    return ReadFileTool(cwd_provider=cwd_provider).to_tool()


class _MainCwdProvider:
    cwd = Path.cwd()


async def main() -> None:
    tool = create_read_file_tool(cwd_provider=_MainCwdProvider())
    tool_input = ReadFileInput(
        filepath="/home/bruce/projects/project-x/demos/temp.txt",
        line_range_start=1,
        line_range_end=20,
    )
    result = await tool.handler(arguments=tool_input.model_dump())
    output = ReadFileOutput.model_validate(result)
    print(output.model_dump_json(indent=2, exclude={"content"}))
    print("content:")
    print(output.content, end="")


if __name__ == "__main__":
    asyncio.run(main())
    # nl -ba /home/bruce/projects/project-x/demos/temp.txt | sed -n '1,20p'