# 功能：
- **异步总结与异步判断是否重置上下文。**
- steer conversation（后期基于这个做multi-agent沟通）
- 暂停（等本轮工具调用结束后暂停），现在AI的输出速度太快了，得加这个功能，方便调试

# 建议阅读顺序：
1. src/core/init_prompts.py 大致了解记忆功能。工作过程、记忆总结和记忆重置决策要分离，减轻AI的注意力负担，这三者看似不能被分离，但其实是可以的，具体见指令。上下文要每增长一定程度就要总结一次，而不是等到最后重置的时候再总结，不然模型会有遗漏
2. src/core/agent.py，这里面是 agent 提供的接口。
3. src/core/agent_runner.py：主要是为了照顾steer conversation功能，保证agent在有多条steer msg进来的时候，只运行一个agent，防止重入。 
4. src/web_app.py
5. src/websocket_chat_session.py

# 文档：
- AGENTS.md 大致讲述项目的结构。要配合 [micropower](https://github.com/jenglong1899/micropowers) 使用
- docs/code_explanations 让 AI 给我解释的一些代码。 用 [structured-knowledge](https://github.com/jenglong1899/structured-knowledge) skill 制作
- docs/feature-decisions 产品功能决策
- docs/draft-plans 我自己写的初步计划
- docs/plans AI基于初步计划制定的计划

# 启动

Linux/WSL/macos:

```
chmod +x dev.sh
./dev.sh
```
