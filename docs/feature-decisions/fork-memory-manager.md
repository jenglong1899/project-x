用户可以主动唤起memory manager，让它去重置。
我们希望 Worker 和 Memory Manager 尽可能分离，就是 Worker 尽量不要涉及去跟记忆相关的事情。
所以是用户主动去跟 Memory Manager 谈，而不是说给 Worker 一个工具让他去唤醒 Memory Manager。

可以在不暴露底层实现的情况下实现这个功能。在system prompt里写了会有专门的memory agent，如果用户有记忆方面的指令，唤起它就行了（平时系统会自动唤起memory agent），这就是一种分离。

system prompt:
```
系统会定期自动fork一个memory manager出来让它工作，但如果用户主动要求worker去做记忆相关的操作，worker也可以调用工具 fork_memory_manager 来主动唤起memory manager来处理记忆
```

当worker主动调用了 fork_memory_manager 后，系统定期自动fork的memory manager的频率也要随之调整。比如前脚刚fork了，然后后脚系统又自动唤起了一次。

这功能不是最先需要的功能，先放着。
