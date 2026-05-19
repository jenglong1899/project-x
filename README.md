功能：
- 异步总结与异步判断是否重置上下文。
- steer conversation（后期基于这个做multi-agent沟通）
- 暂停（等本轮工具调用结束后暂停）

建议阅读顺序：
1. src/core/agent.py，这里面是 agent 提供的接口。
2. src/core/agent_runner.py：主要是为了照顾steer conversation功能，保证agent在有多条steer msg进来的时候，只运行一个agent，防止重入。 
3. src/web_app.py
4. src/websocket_chat_session.py

docs/code_explanations 让 AI 给我解释的一些代码。 用 [structured-knowledge](https://github.com/jenglong1899/structured-knowledge) skill 制作
docs/feature-decisions 产品功能决策
docs/draft-plans 我自己写的初步计划
docs/plans AI基于初步计划制定的计划
