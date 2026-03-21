bionic-claw系统允许创建多个agent（相当于企业中的多个人类员工），每个agent有各自的记忆文件 
~/.bionic-claw/memories/<agent_name>/summaries
~/.bionic-claw/memories/<agent_name>/originals

agent可以创建subagent，subagent没有任何记忆机制，它们是临时创建的，用来给agent减轻上下文压力，subagent只有只读权限

我要做基于Steer conversation做一个multiagent沟通机制。
Agent之间通过user message来沟通，就是通过steer conversation。这样的话你就可以异步地沟通，模型可以选择立刻回复还是稍后回复。用user message而不是tool call，就是因为如果你发了tool call的话，那么你必须在下一轮附上tool result，这样就没法实现异步沟通。
要有一个工具叫做send_msg_to_agent，参数agent_name和msg，底层就是通过enqueue user message
agent A调用这个工具给agent B发送消息时，agent B会收到：
```
<msg from="A">some content</msg>
```
这个xml是系统自动加上的

要有一个工具叫做Create subagent，参数是subagent的名字。还有一个first msg参数，就是就是给这个subagent的第一句话，就是说交给他的任务吧。

应该是有个类似 AgentHub 的抽象，用来存储这些 Agent。不管是 agent 还是 sub agent 都要存在里面。每个 Agent 的 Sub Agent 只对自己可见，不对其他 Agent 可见。

需要提醒用户，如非必要，不要再创建更多的 agent，因为 Agent 的记忆是隔离的，会带来沟通成本。

前端应该是有个 dashboard 用来允许用户创建 agent。

如果是有多个 agent 的话，需要在构建提示词的时候告诉 agent 其他 agent 的存在，并介绍其他 agent 的职责。前端dashboard要有个地方用来填 Agent 的职责（提醒用户这会用来向其他 agent 介绍）。