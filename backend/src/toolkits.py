from __future__ import annotations

from src.commons import SUMMARIES_DIR, ToolCallerKind
from src.tools.tool import Tool
from src.tools.apply_patch import create_apply_patch_tool
from src.tools.bash import create_bash_tool
from src.tools.cwd_state import CwdState
from src.tools.insert_text import create_insert_text_tool
from src.tools.read_file import create_read_file_tool
from src.tools.replace_text import create_replace_text_tool


def _build_tools(*, caller_kind: ToolCallerKind, cwd_state: CwdState, provider: str) -> list[Tool]:
    base_tools: list[Tool] = [
        create_bash_tool(caller_kind=caller_kind, cwd_state=cwd_state),
        create_read_file_tool(cwd_provider=cwd_state),
    ]

    if provider == "openai-codex":
        return [
            *base_tools,
            create_apply_patch_tool(caller_kind=caller_kind, cwd_provider=cwd_state),
        ]

    return [
        *base_tools,
        create_replace_text_tool(caller_kind=caller_kind, cwd_provider=cwd_state),
        create_insert_text_tool(caller_kind=caller_kind, cwd_provider=cwd_state),
    ]


def build_worker_tools(*, cwd_state: CwdState, provider: str) -> list[Tool]:
    return _build_tools(caller_kind="worker", cwd_state=cwd_state, provider=provider)


def build_memory_manager_summary_tools(*, provider: str) -> list[Tool]:
    cwd_state = CwdState(initial_cwd=str(SUMMARIES_DIR))
    return _build_tools(
        caller_kind="memory_manager_summary",
        cwd_state=cwd_state,
        provider=provider,
    )
