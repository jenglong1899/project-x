用户可以主动唤起memory manager，让它去重置。
我们希望 Worker 和 Memory Manager 尽可能分离，就是 Worker 尽量不要涉及去跟记忆相关的事情。
所以是用户主动去跟 Memory Manager 谈，而不是说给 Worker 一个工具让他去唤醒 Memory Manager。

可以在不暴露底层实现的情况下实现这个功能。在system prompt里写了会有专门的memory agent，如果用户有记忆方面的指令，唤起它就行了（平时系统会自动唤起memory agent）
