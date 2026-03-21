import subprocess
from typing import Any

from pydantic import BaseModel, Field

from src.core.agent_turn import ToolSpec


class BashToolInput(BaseModel):
    command: str = Field(min_length=1, description="要执行的 bash 命令")


class BashToolOutput(BaseModel):
    stdout: str
    stderr: str
    returncode: int


def run_bash(*, arguments: dict[str, Any]) -> dict[str, Any]:
    tool_input = BashToolInput.model_validate(arguments)
    completed_process = subprocess.run(
        ["bash", "-lc", tool_input.command],
        capture_output=True,
        text=True,
        check=False,
    )
    return BashToolOutput(
        stdout=completed_process.stdout,
        stderr=completed_process.stderr,
        returncode=completed_process.returncode,
    ).model_dump()


BASH_TOOL = ToolSpec(
    name="bash",
    description="执行一条 bash 命令，并返回标准输出、标准错误和退出码。",
    parameters_json_schema=BashToolInput.model_json_schema(),
    handler=run_bash,
)
