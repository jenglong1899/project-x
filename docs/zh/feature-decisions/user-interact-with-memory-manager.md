用户可以主动唤起memory manager，让它去重置。
我们希望 Worker 和 Memory Manager 尽可能分离，就是 Worker 尽量不要涉及去跟记忆相关的事情。
所以是用户主动去跟 Memory Manager 谈，而不是说给 Worker 一个工具让他去唤醒 Memory Manager。
