from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.commons import ToolCallerKind, assert_allowed_summaries_write
from src.core.agent_turn import Tool

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
                "\n"
                "约束：\n"
                "- 只允许修改当前项目工作区内的相对路径（拒绝绝对路径、包含 `..` 的路径）。\n"
                "- 仍会应用 summaries 写入守卫：worker 只能改 TODO.md，memory manager(summary) 不能改 TODO.md。\n"
                "\n"
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
        self._validate_target_paths(patch)


        result = cap.apply_patch(patch)
        return {"ok": True, "result": str(result)}

    def _validate_target_paths(self, patch: str) -> None:
        if "*** Begin Patch" not in patch or "*** End Patch" not in patch:
            raise ValueError("patch 必须包含 `*** Begin Patch` 与 `*** End Patch`")

        # 工具运行在后端进程中，但要允许编辑整个仓库（包含 frontend/ 等），
        # 所以这里把“项目工作区根目录”定义为仓库根（backend/ 的上一级）。
        project_root = Path(__file__).resolve().parents[4]
        for raw in _extract_patch_paths(patch):
            path = Path(raw)
            if path.is_absolute():
                raise ValueError(f"apply_patch 禁止使用绝对路径：{raw!r}")
            if any(part == ".." for part in path.parts):
                raise ValueError(f"apply_patch 禁止使用包含 '..' 的路径：{raw!r}")

            resolved = (project_root / path).resolve()
            try:
                resolved.relative_to(project_root)
            except ValueError as exc:
                raise ValueError(f"apply_patch 目标路径越界：{raw!r}") from exc

            assert_allowed_summaries_write(caller_kind=self._caller_kind, target_path=resolved)


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
