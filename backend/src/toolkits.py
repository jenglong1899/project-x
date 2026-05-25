from __future__ import annotations

from src.commons import SUMMARIES_DIR
from src.core.agent_turn import Tool
from src.tools.bash import create_bash_tool
from src.tools.cwd_state import CwdState
from src.tools.insert_text import create_insert_text_tool
from src.tools.read_file import create_read_file_tool
from src.tools.replace_text import create_replace_text_tool


def build_worker_tools(*, cwd_state: CwdState) -> list[Tool]:
    return [
        create_bash_tool(caller_kind="worker", cwd_state=cwd_state),
        create_read_file_tool(cwd_provider=cwd_state),
        create_replace_text_tool(caller_kind="worker", cwd_provider=cwd_state),
        create_insert_text_tool(caller_kind="worker", cwd_provider=cwd_state),
    ]


def build_memory_manager_summary_tools() -> list[Tool]:
    cwd_state = CwdState(initial_cwd=str(SUMMARIES_DIR))
    return [
        create_bash_tool(caller_kind="memory_manager_summary", cwd_state=cwd_state),
        create_read_file_tool(cwd_provider=cwd_state),
        create_replace_text_tool(caller_kind="memory_manager_summary", cwd_provider=cwd_state),
        create_insert_text_tool(caller_kind="memory_manager_summary", cwd_provider=cwd_state),
    ]

