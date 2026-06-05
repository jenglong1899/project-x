from __future__ import annotations

from src.commons import SUMMARIES_DIR, ToolCallerKind
from src.tools.tool import Tool
from src.tools.apply_patch import create_apply_patch_tool
from src.tools.bash import create_bash_tool
from src.tools.cwd_state import CwdState
from src.tools.insert_text import create_insert_text_tool
from src.tools.mock_customer_service import (
    create_check_refund_policy_tool,
    create_escalate_to_human_tool,
    create_get_order_status_tool,
)
from src.tools.read_file import create_read_file_tool
from src.tools.replace_text import create_replace_text_tool


def _build_tools(*, caller_kind: ToolCallerKind, cwd_state: CwdState, provider: str) -> list[Tool]:
    base_tools: list[Tool] = [
        create_bash_tool(caller_kind=caller_kind, cwd_state=cwd_state),
        # create_read_file_tool(cwd_provider=cwd_state),
    ]
    if caller_kind == "worker":
        base_tools.extend([
            create_get_order_status_tool(),
            create_check_refund_policy_tool(),
            create_escalate_to_human_tool(),
        ])

    if provider == "openai-codex":
        return [
            *base_tools,
            # create_apply_patch_tool(caller_kind=caller_kind, cwd_provider=cwd_state),
        ]

    return [
        *base_tools,
        create_replace_text_tool(caller_kind=caller_kind, cwd_provider=cwd_state),
        create_insert_text_tool(caller_kind=caller_kind, cwd_provider=cwd_state),
    ]


def build_worker_tools(*, cwd_state: CwdState, provider: str) -> list[Tool]:
    return _build_tools(caller_kind="worker", cwd_state=cwd_state, provider=provider)


def build_memory_manager_summarizer_tools(*, provider: str) -> list[Tool]:
    cwd_state = CwdState(initial_cwd=str(SUMMARIES_DIR))
    return _build_tools(
        caller_kind="summarizer",
        cwd_state=cwd_state,
        provider=provider,
    )
