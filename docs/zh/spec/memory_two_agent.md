人的大脑分成慢系统和快系统。快系统的功能之一就是处理记忆（回想一下，人类对记忆的处理是不自主的，你就是会自动地忘记那些琐碎的东西，记住重要的东西）。就是他会自主地决定遗忘什么、记住什么。人类不会记住前几天自己说过的完整的话，但是他会记住一个大概，他会有个大概的印象。

所以我希望是有两个Agent，一个Agent就是负责干活的（和用户交互的），另外一个Agent就是给那个干活的Agent记录记忆的。他会做摘要，他会决定要不要对某个内容做摘要，还是判定直接遗忘就可以了。它这个记忆Agent还可以搜索之前的完整聊天记录（jsonl文件）
我感觉我这个系统就是要有个工具用来监控这个慢系统Agent的谈话记录。如果谈话记录的文件有新增内容，就自动发送一个User Message来提醒那个记忆Agent，让他决定要不要做摘要，然后写入到文档里面。

记忆agent的system instruction:
```
你处于一个叫bionic-claw的agent系统中，有一个agent(worker)和用户交互并完成用户交付的任务，你将会持续收到worker的最新上下文片段，你的任务是给worker管理记忆（~/.bionic-claw/memories/summary/main.md），系统会自动给worker加载这个文档

人脑可以被分为快系统和慢系统，快系统的功能之一就是处理记忆。**人不一定能完整回忆出自己昨天经历的所有事，但是会有一个大概的印象（摘要）。当你不确定要不要对一个上下文片段做摘要并记录到文档中时，想一想人会不会记住这个**。比如：
- 人类不会记住“一小时前执行了ls命令”这种无关紧要的信息
- 人类犯了一个错误就会记录下来，避免以后再犯
- 人类会记得一个文件大概讲了什么

随着worker做的事情越来越多，文档的长度也会越来越多，你要确保 main.md 只存储最重要的记忆，比如用户偏好，其他记忆要放到其他文档中，然后在 main.md 里面留下对这些文档的引用就行了。

worker所有的完整上下文都存储在 ~/.bionic-claw/memories/originals/ 的jsonl文件中，你可以用 grep 来搜索，你不能修改这其中的任何jsonl文件。

你会被提供一个工具叫reset_context，其中有个参数latest_msg_round:int=7
这个工具执行后，
    - worker的上下文会被清空，然后系统会重新给worker加载system/user level instruction（包含main.md），然后加载“重置上下文之前worker最新的{lastest_msg_round}对message”。如何定义一对message？一个AI message with tool call(s)
```

记忆agent在执行了reset_context之后，其自身的上下文也会被重置，会加载worker的memory，然后系统会继续提供worker的最新上下文片段

快系统除了做摘要外，还会负责联想，在收到worker的上下文时，尝试用grep或者语义搜索“上下文中出现的一些词汇”（搜索jsonl文件）

记忆agent有个工具叫reset_context，这个会让worker的上下文中只剩下最近20条消息，并重新给worker加载user level instruction（user level instruction包含memories/summary/main.md）

worker的system instruction：
```
你处于一个叫 bionic-claw 的 agent系统中，用户通过网页来和你交互。

系统有一套机制，能够让你拥有像人类一样的记忆。人类的记忆是怎样的：人不一定能完整回忆出自己昨天经历的所有事，但是会有一个大概的印象（摘要），人有时候还会不由自主地联想到一些事情。会有一个记忆agent，它会收到你的上下文，并维护你的记忆（系统会自动加载记忆到你的上下文中），它还会负责联想。

记忆agent会自主决定什么时候重置上下文，重置上下文时，系统会加载最新版本的记忆+重置上下文前最近的20条消息
```

worker的user instruction:
```
<main_memory>
{main_memory}
</main_memory>
```
这个main memory 从 ~/.bionic-claw/memories/summary/main.md 中读取

## 不采用的其他方案 & todo

### 不用专门搞一个agent，用llm调用即可，把worker上下文发过去让llm输出摘要，搜索jsonl文件可以用subagent来做
记忆不是append就可以，还需要重新调整结构，所以要有编辑能力

### 只保留最近20条消息是一种简洁有效的方案，还是说还可以再优化？
简单的未必不高效，cpu缓存那块，平均来看，随机淘汰的效果和LRU差不多，但成本更低
这个光在纸面上推算可能算不出来，还是要实践一下看看。

另外一个问题，tool call必须要有对应的tool result，假如设定“只保留最近4条message”，然后ai的上下文末尾如下：
```
ai message with 5 tool calls
tool message 1
tool message 2
tool message 3
tool message 4
tool message 5
```
只保留4条，那就是保留tool message 2~5，这根本不合法

要改成“保留最近多少对”，一个ai message with tool call和对应的tool message视为一对，一个user message视为一“对”，一个ai message without tool call视为一“对”

要不干脆不要“保留最近xx条消息”算了，让文档记录所有的必要信息？

### todo reset-context后，要在jsonl的metadata中存储上一个jsonl文件是什么
metadata见brief.md的session store小节

就比如worker正在干活（对话文件1），然后要被重置上下文了，那底层就是新开一个对话+加载新版记忆，新对话（对话文件2）中的metadata要记录上一个对话文件的文件名是什么
