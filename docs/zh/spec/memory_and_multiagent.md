# multi-agent
我要做基于Steer conversation做一个multiagent机制。
Agent之间通过user message来沟通，就是通过steer conversation。这样的话你就可以异步地沟通，模型可以选择立刻回复还是稍后回复。用user message而不是tool call，就是因为如果你发了tool call的话，那么你必须在下一轮附上tool result，这样就没法实现异步沟通。
要有一个工具叫做Create subagent，参数是agent的名字。还有一个first msg参数，就是就是给这个subagent的第一句话，就是说交给他的任务吧。
要有一个工具叫做send message to agent，参数agent_name和msg，底层就是通过enqueue user message
系统要用XML包裹msg，类似这样 <msg from="agent_name"> some content </msg> 

# 新版记忆机制
另外一个我要做的东西就是基于这个multiagent，我想做一个和现在市面上常见的记忆机制不一样的记忆机制
原理就和人脑类似。人的大脑分成慢系统和快系统。快系统的功能之一就是他会搞那个记忆嘛。就是他会自主地决定遗忘什么、记住什么。人类不会记住前几天自己说过的就是完整的话，但是他会记住一个大概，他会有个大概的印象。
你是可以在指令里面告诉Agent要频繁地记录自己的记忆，但是这样会让他分心。所以我希望是有两个Agent，一个Agent就是负责干活的（和用户交互的），另外一个Agent就是给那个干活的Agent记录记忆的。他会做摘要，他会决定要不要对某个内容做摘要，还是判定直接遗忘就可以了。它这个记忆Agent还可以搜索之前的完整聊天记录（jsonl文件）
我感觉我这个系统就是要有个工具用来监控这个慢系统Agent的谈话记录。如果谈话记录的文件有新增内容，就自动发送一个User Message来提醒那个记忆Agent，让他决定要不要做摘要，然后写入到文档里面。

记忆agent的system instruction:
```
你处于一个叫bionic-claw的agent系统中，有一个agent和用户交互并完成用户交付的任务，我们称这个agent为worker，你将会持续收到worker的最新上下文片段，你的任务是给worker管理记忆。

系统会自动给worker加载 ~/.bionic-claw/memories/summary/main.md

人脑可以被分为两个系统，快系统和慢系统，快系统的功能之一就是处理记忆。**人不一定能完整回忆出自己昨天经历的所有事，但是会有一个大概的印象（摘要）。当你不确定要不要对一个上下文片段做摘要并记录到文档中时，想一想人会不会记住这个**。比如：
- 人类不会记住“一小时前执行了ls命令”这种无关紧要的信息
- 人类犯了一个错误就会记录下来，避免以后再犯

随着worker做的事情越来越多，文档的长度也会越来越多，你要确保 main.md 只存储最重要的记忆，比如用户偏好，其他记忆要放到其他文档中，然后在 main.md 里面留下对这些文档的引用就行了。

worker所有的完整上下文都存储在 ~/.bionic-claw/memories/originals/ 的jsonl文件中，你可以用 grep 来搜索，你不能修改这其中的任何jsonl文件。
```

记忆agent有个工具叫reset_context，这个会让worker的上下文中只剩下最近20条消息，并重新给worker加载user level instruction（user level instruction包含memories/summary/main.md）

worker的system instruction：
```
你处于一个叫 bionic-claw 的 agent系统中，用户通过网页来和你交互。

系统有一套机制，能够让你拥有像人类一样的记忆。人类的记忆是怎样的：人不一定能完整回忆出自己昨天经历的所有事，但是会有一个大概的印象（摘要）。会有一个记忆agent专门给你管理记忆，它会收到你的上下文，并维护你的记忆（系统会自动加载记忆到你的上下文中）

记忆agent会自主决定什么时候重置上下文，重置上下文时，系统会加载最新版本的记忆+重置上下文前最近的20条消息
```

## 不采用的其他方案 & todo

### 不用专门搞一个agent，用llm调用即可，把worker上下文发过去让llm输出摘要，搜索jsonl文件可以用subagent来做
记忆不是append就可以，还需要重新调整结构，所以要有编辑能力

### 只保留最近20条消息是一种简洁有效的方案，还是说还可以再优化？
简单的未必不高效，cpu缓存那块，平均来看，随机淘汰的效果和LRU差不多，但成本更低
这个光在纸面上推算可能算不出来，还是要实践一下看看。
