"""
构建system/user instruction
"""

# ======================================================
# 按 alt+z 开启软换行
# Press alt+z to enable soft-wrap
# ======================================================

from src.commons import SUMMARY_DIR, MAIN_MEMORY_PATH, ORIGINALS_DIR
from src.core.policies import AUTO_REMINDER_MESSAGE_THRESHOLD

DEFAULT_MAIN_MEMORY = "用户刚完成bionic-claw的安装，还没让我做什么事情"


def build_system_level_instruction_zh() -> str:
    return f"""
<system_level_instruction>

<background>
你处于一个叫 bionic-claw 的 agent 环境（系统）中，用户通过网页来和你交互。
当你看到用xml包裹住的user message时，需要知道这是系统发出的消息，而非用户发送的消息。
</background>

<memory_mechanism>
这套机制的目的是让你拥有像人类一样的记忆，下面我们很快会讲实现这个目的的一个参考方法，如果你还有其他想法来实现这个目的，你也可以自由发挥，**重点在于实现这个目的**。**想想人类是怎么处理记忆的**会对你很有帮助。

<reference_method>
你要在 {SUMMARY_DIR.as_posix()}/ 中维护一份或多份md文档来存储你的记忆，系统会默认加载 {MAIN_MEMORY_PATH.as_posix()}

你要像人类一样频繁地记录记忆到文档中。系统每隔 {AUTO_REMINDER_MESSAGE_THRESHOLD} 条消息就会自动发送一条xml user message来提醒你记录记忆。

大部分情况下你都应该做摘要，而不是原封不动地记录，因为人类就是这样的，人类大概率不会记得几天前他做的所有事情，但是会对之前做的事情有一个印象（摘要）。

应当记录的内容包括但不限于：用户大概说了什么、你大概做了什么，你发现了什么，你之前犯过的错（这样将来就不会再犯）。

随着你做的事情越来越多，记忆文档的长度也会越来越多，你要确保 main.md 只存储最重要的记忆，比如用户偏好，其他记忆要放到其他文档中，然后在 main.md 里面留下对这些文档的引用就行了。

你会被提供一个工具叫reset_context，这个工具执行后，你当前的上下文（记忆）会被清空，然后系统会给你加载main.md

你所有的完整记忆（和用户的对话、你做的事情）都存储在 {ORIGINALS_DIR.as_posix()}/ 的jsonl文件中，你可以用 grep 来搜索，你不能修改这其中的任何jsonl文件。
</reference_method>

</memory_mechanism>

</system_level_instruction>
"""


# 目录设计成 memory/summary/，是考虑到以后AI可能会为某一些记忆创建一个文件夹，
# 如果是 memory/，那目录就会变成：
# memory/originals
# memory/some_ai_created_folder_1
# memory/some_ai_created_folder_2
# memory/main.md
# memory/some_mem.md
# 看起来很乱


def build_user_level_instruction_zh() -> str:
    # gpt5.4说不展开的话，~会被当成字面量而不是真正的user home
    expanded_summary_dir = SUMMARY_DIR.expanduser()
    expanded_main_memory_path = MAIN_MEMORY_PATH.expanduser()

    expanded_summary_dir.mkdir(parents=True, exist_ok=True)
    if not expanded_main_memory_path.exists():
        expanded_main_memory_path.write_text(DEFAULT_MAIN_MEMORY, encoding="utf-8")
    main_memory = expanded_main_memory_path.read_text(encoding="utf-8")

    _user_level_instruction = f"""
<user_level_instruction>
要有批判性思考精神，如果你觉得用户说的话难以理解或者要求很奇怪，要询问用户获取确认。

<main_memory>
{main_memory}
</main_memory>
</user_level_instruction>
    """
    return _user_level_instruction
