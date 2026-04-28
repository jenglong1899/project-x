有一个新的记忆机制，你去看一下 backend/src/prompts/builder.py 就知道了

到时候我们也不需要搞什么 reset context tool 了，我们直接从 agent 的输出中识别对应的 MagicWord，然后就执行对应的就行了。

我们要实现了一个像人类一样的记忆，其实人类可没有什么 new conversation 和 resume conversation 这种东西，我们把它当做一个无限长的 conversation 就行了。

所以 agent.py 里面的 resume conversation 要删掉。new conversation 不再对外暴露，它会变成一个private函数，它会在创建一个 agent 的时候被使用，reset-context时被使用

从外部看来没有这两个接口，但是内部实现中，我们还是要有 conversation 这种概念，也就是每个 conversation 要存放在单独的 json 文件中。
