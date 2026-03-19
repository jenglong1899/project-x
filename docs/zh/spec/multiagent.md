# multi-agent
我要做基于Steer conversation做一个multiagent机制。
Agent之间通过user message来沟通，就是通过steer conversation。这样的话你就可以异步地沟通，模型可以选择立刻回复还是稍后回复。用user message而不是tool call，就是因为如果你发了tool call的话，那么你必须在下一轮附上tool result，这样就没法实现异步沟通。
要有一个工具叫做Create subagent，参数是agent的名字。还有一个first msg参数，就是就是给这个subagent的第一句话，就是说交给他的任务吧。
要有一个工具叫做send message to agent，参数agent_name和msg，底层就是通过enqueue user message
系统要用XML包裹msg，类似这样 <msg from="agent_name"> some content </msg> 

