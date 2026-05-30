import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, Field

from src.commons import ToolCallerKind
from src.tools.tool import Tool
from src.tools.cwd_state import CwdState, persist_worker_cwd


class BashToolInput(BaseModel):
    command: str = Field(min_length=1, description="要执行的 bash 命令")


class BashToolOutput(BaseModel):
    stdout: str
    stderr: str
    returncode: int


class BashTool:
    def __init__(
        self,
        *,
        caller_kind: ToolCallerKind,
        initial_cwd: str | None = None,
        cwd_state: CwdState | None = None,
    ) -> None:
        self._caller_kind = caller_kind
        self._cwd_state = cwd_state or CwdState(initial_cwd=initial_cwd)

    def to_tool(self) -> Tool:
        return Tool(
            name="bash",
            description="执行一条 bash 命令，并返回标准输出、标准错误和退出码。"
                        "本工具能记住cwd，当你执行`cd <some_path> && <some_command>`时，后面不用再写`cd <some_path>`。"
                        "`read`和`apply_patch`工具同样沿用cwd。",
            parameters_json_schema=BashToolInput.model_json_schema(),
            handler=self.run,
        )

    async def run(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._caller_kind == "memory_manager_summary":
            raise ValueError("memory manager (summary) 禁止调用 bash；请改用 read_file/replace_text/insert_text")

        tool_input = BashToolInput.model_validate(arguments)
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "bash-state"
            # 每次 tool call 都是一个新 bash 进程，cd 本身不会跨进程保留。
            # 所以在用户命令执行完后，让同一个 bash 进程把“用户命令退出码 + 最终 PWD”
            # 写到仅给 Python 读取的临时文件里，避免把内部状态协议混进 stdout 给 AI 看。
            # $? 是上一条命令的退出码；exit 再用这个退出码结束 wrapper，保持工具语义不变。
            wrapped_command = (
                f"{tool_input.command}\n"
                f"__project_x_returncode=$?\n"
                f'printf "%s\\n%s\\n" "$__project_x_returncode" "$PWD" > "{state_path}"\n'
                f"exit $__project_x_returncode"
            )
            process = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                wrapped_command,
                cwd=str(self._cwd_state.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await process.communicate()
            returncode, cwd = self._read_state_file(
                state_path=state_path,
                fallback_returncode=process.returncode or 0,
            )
            self._cwd_state.cwd = cwd
            if self._caller_kind == "worker":
                persist_worker_cwd(cwd=cwd)
        return BashToolOutput(
            stdout=(stdout_bytes or b"").decode(),
            stderr=(stderr_bytes or b"").decode(),
            returncode=returncode,
        ).model_dump()

    def _read_state_file(self, *, state_path: Path, fallback_returncode: int) -> tuple[int, Path]:
        if not state_path.exists():
            return fallback_returncode, self._cwd_state.cwd

        lines = state_path.read_text().splitlines()
        if len(lines) < 2:
            return fallback_returncode, self._cwd_state.cwd
        return int(lines[0]), Path(lines[1]).resolve()


def create_bash_tool(
    *,
    caller_kind: ToolCallerKind = "worker",
    initial_cwd: str | None = None,
    cwd_state: CwdState | None = None,
) -> Tool:
    return BashTool(caller_kind=caller_kind, initial_cwd=initial_cwd, cwd_state=cwd_state).to_tool()
