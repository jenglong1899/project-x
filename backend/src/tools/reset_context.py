from typing import Any

from src.core.agent_turn import ToolSpec


RESET_CONTEXT_AUTO_REMINDER = "<auto_reminder>你刚才进行了一次重置上下文操作，请继续进行任务</auto_reminder>"


RESET_CONTEXT_FIRST_CALL_HINT = """维护记忆文档非常重要，由于这是当前上下文中你第一次调用本工具，当前调用不会执行重置操作，请先检查：
- 重置上下文的时机：对上下文做了摘要后，大概是原始内容的50%以内吗？
- 记录进记忆文档的信息：
    - 当前记录了的内容能否保证你重置记忆以后还能像之前那样继续工作，仿佛这个重置什么没发生过一样？
    - 人类会记住哪些内容？
        - 人类不会记住“一小时前执行了ls命令”这种无关紧要的信息
        - 人类犯了一个错误就会记录下来，避免以后再犯
        - 人类会记得一个文件大概讲了什么
        - 人类会记住自己的 todo list
- 当前文档的结构是整洁有序的吗？

这些值得你花时间去检查、处理，你要注重质量，而不是速度。

下一次调用本工具时将会执行重置上下文操作。
"""


def _run_reset_context(*, arguments: dict[str, Any]) -> dict[str, Any]:
    # 注意：reset_context 的真实编排逻辑在 Agent._reset_context() 中实现。
    # 这里的 handler 不应被执行（execute_tool_calls 会对 reset_context 做特判）。
    _ = arguments
    return {"hint": None}


async def run_reset_context(*, arguments: dict[str, Any]) -> dict[str, Any]:
    return _run_reset_context(arguments=arguments)


RESET_CONTEXT_TOOL = ToolSpec(
    name="reset_context",
    description=(
        "重置上下文：重新加载 system/user instruction，并切换到新的会话继续任务。"
        "注意：本工具不能与其他工具并发调用，同一条 assistant message 的 tool_calls 中必须只有 reset_context。"
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    handler=run_reset_context,
)
