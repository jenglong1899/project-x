from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.commons import MEMORY_MAIN_MD, WAKE_MEMORY_MANAGER_FLAG
from src.core.init_prompts import read_main_memory

from src.commons import noop
from src.core.agent_turn import Tool, execute_tool_calls, stream
from src.core.memory_manager_run_logger import MemoryManagerRunLogger
from src.core.model_config import ModelConfig

RESET_CONTEXT_MAGIC_WORD = "PROJECT-X-RESET-CONTEXT"


class MemoryManagerSummaryRunner:
    async def run(
            self,
            *,
            worker_messages: list[dict[str, Any]],
            model_config: ModelConfig,
            tools: list[Tool],
            is_first_time_awaken: bool,
            conversation_file_name: str,
            awaken_round: int,
    ) -> None:
        forked_messages = [dict(message) for message in worker_messages]
        logger = MemoryManagerRunLogger(
            conversation_file_name=conversation_file_name,
            runner_kind="summary",
            awaken_round=awaken_round,
        )
        user_prompt = {
            "role": "user",
            "content": build_memory_manager_summary_prompt(
                is_first_time_awaken=is_first_time_awaken,
            ),
        }
        forked_messages.append(
            {
                **user_prompt,
            }
        )
        logger.append_event(user_prompt)

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
            logger.append_event(
                {
                    "role": "assistant",
                    "content": "\n".join(
                        s for s in [assistant_message.get("reasoning_content"), assistant_message.get("content")] if isinstance(s, str) and s
                    ),
                    "tool_calls": assistant_message.get("tool_calls") or [],
                }
            )
            if not assistant_message.get("tool_calls"):
                break

            tool_messages = await execute_tool_calls(
                ai_msg_dict=assistant_message,
                tools_by_name=tools_by_name,
                on_tool_result=noop,
            )
            forked_messages.extend(tool_messages)
            for tool_message in tool_messages:
                logger.append_event(
                    {
                        "role": "tool",
                        "content": tool_message.get("content"),
                        "tool_call_id": tool_message.get("tool_call_id"),
                    }
                )

        return None


class MemoryManagerJudgeResetContextRunner:
    async def run(
            self,
            *,
            worker_messages: list[dict[str, Any]],
            model_config: ModelConfig,
            tools: list[Tool],
            conversation_file_name: str,
            awaken_round: int,
    ) -> bool:
        forked_messages = [dict(message) for message in worker_messages]
        logger = MemoryManagerRunLogger(
            conversation_file_name=conversation_file_name,
            runner_kind="judge",
            awaken_round=awaken_round,
        )
        user_prompt = {
            "role": "user",
            "content": build_memory_manager_judge_whether_reset_context_prompt(),
        }
        forked_messages.append(
            {
                **user_prompt,
            }
        )
        logger.append_event(user_prompt)

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
            logger.append_event(
                {
                    "role": "assistant",
                    "content": "\n".join(
                        s for s in [assistant_message.get("reasoning_content"), assistant_message.get("content")] if isinstance(s, str) and s
                    ),
                    "tool_calls": assistant_message.get("tool_calls") or [],
                }
            )
            tool_calls = assistant_message.get("tool_calls")
            if not tool_calls:
                break

            # judge runner 只判断是否 reset-context，不允许真正执行工具。
            # 但为了不破坏 provider 的“工具缓存”，我们依然把 tools 透传给 stream，
            # 并对 tool_calls 统一回一个不可执行的 tool result，让对话继续走到结束。
            tool_messages: list[dict[str, Any]] = []
            for call in tool_calls:
                tool_call_id = call.get("id")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    continue
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": "判断是否需要重置上下文，不需要工具调用",
                }
                tool_messages.append(tool_message)
                logger.append_event(
                    {
                        "role": "tool",
                        "content": tool_message["content"],
                        "tool_call_id": tool_call_id,
                    }
                )
            forked_messages.extend(tool_messages)

        content = assistant_message.get("content")
        return isinstance(content, str) and RESET_CONTEXT_MAGIC_WORD in content.splitlines()


def build_memory_manager_summary_prompt(is_first_time_awaken: bool) -> str:
    if is_first_time_awaken:
        memory_operation_history_prompt = f"这是你第一次在当前会话中被唤醒，“磁盘中的{MEMORY_MAIN_MD}”和“上下文中的{MEMORY_MAIN_MD}”是一致的，没有被之前的你修改过"
    else:
        memory_operation_history_prompt = f"""
这不是你第一次在当前会话中被唤醒，你之前已经处理过记忆文档。

你上一次被唤醒的地方是*最近的*那条 {WAKE_MEMORY_MANAGER_FLAG} 消息，在那之前的内容都已经被之前的你摘要过了。

这是当前 {MEMORY_MAIN_MD} 的内容（你等会不需要再调用工具去读一遍了）：
<{MEMORY_MAIN_MD}>
{read_main_memory()}
</{MEMORY_MAIN_MD}>
"""

    return f"""
<roles_change_notice>

**先停下你手头上的事，阅读下面的消息**

**你的角色是memory manager，你刚从worker的上下文中被 fork 出来。**

你现在要做的事情就是处理记忆文档（对当前上下文做摘要然后放到记忆文档中、整理记忆文档等等），之前的指令里除了<memory_mechanism>部分，其他的通通可以忽略掉。

你要达成的目标是：让worker拥有像人类一样的记忆。

下面要描述的是启发式规则，没有穷尽所有应当做或者不做的事。实际执行时，应优先判断怎样做最有利于实现目标。如果某个做法虽然没有被明确写入本文档，但明显有助于目标，就要采用；如果某条具体规则在当前场景下反而妨碍实现目标，就要调整。

<heuristic_rules>

1. 想想怎样做摘要能保证worker重置记忆以后还能像之前那样继续工作，仿佛这个重置什么没发生过一样。

2. 想想人类会怎么记住什么，比如：
    - 人类不会记住“一小时前执行了ls命令”这种无关紧要的信息
    - 人类会记住重复的工作流程。比如一个新手创业者不知道要如何去记账报税，那么他会搜资料，然后他会把那些资料中有用的内容收集起来，写在文档里面，这样以后就不用再去搜一遍资料了。
    - 人类犯了一个错误就会记录下来，避免以后再犯
    - 人类会记得一个文件大概讲了什么
    
3. 随着worker做的事情越来越多，应当被记录进记忆文档的东西也会越来越多，你要确保 {MEMORY_MAIN_MD} 只存储最重要的记忆，其他记忆要放到其他文档中，然后在 {MEMORY_MAIN_MD} 里面留下对这些文档的引用就行了。这里的引用不是说所有的其他记忆文档都要被 {MEMORY_MAIN_MD} 直接引用，而是可以被间接引用，比如有20个文档都是关于某个主题的，要把它们都放进一个文件夹里面，然后在 {MEMORY_MAIN_MD} 里面引用这个文件夹就行。

4. 如果记忆有点散乱了，要把它整理成结构化的。因为杂乱无章的记忆会影响worker的发挥和你的后续维护。

</heuristic_rules>

{memory_operation_history_prompt}

</roles_change_notice>
"""

def build_memory_manager_judge_whether_reset_context_prompt() -> str:
    return f"""
<roles_change_notice>

**先停下你手头上的事，阅读下面的消息**

**你的角色是memory manager，你刚从worker的上下文中被 fork 出来**

你现在要做的事情就是判断当前是否要重置上下文，之前的指令里除了<memory_mechanism>部分，其他的通通忽略掉

判断是否要重置上下文的标准：如果当前上下文中有50%以上的内容都是对当前任务不重要的，那通常就要重置。（这里的50%是按token估算）

一个例子是，当前上下文中有大量的中间过程，而我们只需要最后的结果，那通常就应该重置。

例外情况：如果预估worker还有几轮就可以完成任务，而这时刚好大约有50%的内容是不重要的，那么这个时候一般不建议重置。

如果判断出要重置上下文，你就输出 {RESET_CONTEXT_MAGIC_WORD} ，系统检测到后，就会重置

你可能会在上下文中看到 {WAKE_MEMORY_MANAGER_FLAG}，你不需要去管这个

</roles_change_notice>
"""


# todo 不要提供无用的信息？还是尽可能提供信息？
# AI似乎可以自己估算出来个大概（至少gpt5是这样），所以先不用 _build_context_token_detail ？

def _build_context_token_detail(messages: dict[str, Any]) -> str:
    # 打印以下消息占据的上下文百分比窗口
    # - user msg
    # - AI reasoning 占据多少百分比
    # - AI content占据多少
    # - AI tool call，且其内部还会再细分各个工具的占比（大于一定比例才显示，小于一定比例的，通通归为“其他工具”）
    # - tool result 占据多少百分比，且其内部还会再细分各个工具的tool result的占比（大于一定比例才显示，小于一定比例的，通通归为“其他工具”）
    raise NotImplementedError
