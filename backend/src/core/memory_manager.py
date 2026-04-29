from dataclasses import dataclass
from typing import Any, Protocol

from src.commons import noop
from src.core.agent_turn import ToolSpec, execute_tool_calls, stream
from src.core.model_config import ModelConfig
from src.prompts.builder import build_memory_forked_subagent_prompt


RESET_CONTEXT_MAGIC_WORD = "PROJECT-X-RESET-CONTEXT"


@dataclass(frozen=True)
class MemoryManagerResult:
    requested_reset_context: bool


class MemoryForkedSubagentRunnerLike(Protocol):
    async def run(
        self,
        *,
        worker_messages: list[dict[str, Any]],
        model_config: ModelConfig,
        tools: list[ToolSpec],
        is_first_time_awaken: bool,
        loaded_main_memory_content: str,
    ) -> MemoryManagerResult: ...


class MemoryForkedSubagentRunner:
    async def run(
        self,
        *,
        worker_messages: list[dict[str, Any]],
        model_config: ModelConfig,
        tools: list[ToolSpec],
        is_first_time_awaken: bool,
        loaded_main_memory_content: str,
    ) -> MemoryManagerResult:
        forked_messages = [
            dict(message)
            for message in worker_messages
        ]
        forked_messages.append(
            {
                "role": "user",
                "content": build_memory_forked_subagent_prompt(
                    is_first_time_awaken=is_first_time_awaken,
                    loaded_main_memory_content=loaded_main_memory_content,
                ),
            }
        )

        tools_by_name = {tool.name: tool for tool in tools}
        if len(tools_by_name) != len(tools):
            raise ValueError("tools 里存在重复的 name")

        while True:
            assistant_message = await stream(
                model_config=model_config,
                messages=forked_messages,
                tools=tools,
                on_ai_content_delta=noop,
                on_ai_reasoning_delta=noop,
                on_ai_tool_call_started=noop,
                on_ai_tool_call_arguments_delta=noop,
                on_ai_tool_call_finished=noop,
            )
            forked_messages.append(assistant_message)
            if not assistant_message.get("tool_calls"):
                break

            tool_execution = await execute_tool_calls(
                ai_msg_dict=assistant_message,
                tools_by_name=tools_by_name,
                on_tool_result=noop,
            )
            forked_messages.extend(tool_execution.tool_messages)

        content = assistant_message.get("content")
        requested_reset_context = (
            isinstance(content, str)
            and RESET_CONTEXT_MAGIC_WORD in content.splitlines()
        )
        return MemoryManagerResult(
            requested_reset_context=requested_reset_context,
        )
