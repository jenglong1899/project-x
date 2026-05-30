from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.commons import ToolCallerKind, assert_allowed_summaries_write
from src.tools.tool import Tool

import codex_apply_patch as cap


class ApplyPatchInput(BaseModel):
    patch: str = Field(min_length=1)


class ApplyPatchTool:
    def __init__(self, *, caller_kind: ToolCallerKind) -> None:
        self._caller_kind = caller_kind

    def to_tool(self) -> Tool:
        return Tool(
            name="apply_patch",
            description=(
                "按 Codex 的 `*** Begin Patch` 格式对工作区文件应用补丁。\n"
                "输入 patch 示例：\n"
                "*** Begin Patch\n"
                "*** Update File: backend/src/foo.py\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            ),
            parameters_json_schema=ApplyPatchInput.model_json_schema(),
            handler=self.run,
        )

    async def run(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_input = ApplyPatchInput.model_validate(arguments)
        patch = tool_input.patch
        assert_allowed_summaries_write(caller_kind=self._caller_kind, target_path=resolved)
        result = cap.apply_patch(patch)
        return {"ok": True, "result": str(result)}


def _extract_patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("*** Add File: "):
            paths.append(line.removeprefix("*** Add File: ").strip())
            continue
        if line.startswith("*** Update File: "):
            paths.append(line.removeprefix("*** Update File: ").strip())
            continue
        if line.startswith("*** Delete File: "):
            paths.append(line.removeprefix("*** Delete File: ").strip())
            continue
        if line.startswith("*** Move to: "):
            paths.append(line.removeprefix("*** Move to: ").strip())
            continue
    return [p for p in paths if p]


def create_apply_patch_tool(*, caller_kind: ToolCallerKind) -> Tool:
    return ApplyPatchTool(caller_kind=caller_kind).to_tool()
