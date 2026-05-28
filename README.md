# 概述

本 Agent 系统的重点是记忆机制，有 worker 和 memory manager 两个角色，任务执行和记忆分离，从而减轻 AI 的注意力负担，达到更好的工作效果和记忆效果。

上下文每增长 3%，系统就从 worker 中 fork 两个 memory manager 出来异步工作，一个负责总结，一个负责决定是否要重置上下文。（等模型决定重置后再统一总结会导致遗漏）

这个思路来源于人脑，人脑有两个系统：快系统（潜意识、自动运行）、慢系统。快系统负责处理记忆，人遇到罕见的情况，就会自动记住。

memory manager相当于快系统，worker相当于慢系统

预计一年后，等大模型价格大幅下降的时候，可以调整成慢系统每工作5轮就唤起一次快系统，甚至每工作一轮就唤起一次快系统

# 建议阅读顺序：

1. src/core/init_prompts.py 和 src/core/memory_manager.py
2. src/core/agent.py，Pycharm里面点击Structure, VSCode里面点击Outline来查看 Agent 对外暴露了什么接口。核心是 run()
3. src/core/agent_runner.py：主要是为了照顾steer conversation功能，保证agent在有多条steer msg进来的时候，只运行一个agent，防止重入。 
4. src/web_app.py
5. src/websocket_chat_session.py

# 其他功能：
- steer conversation（后期基于这个做multi-agent沟通）
- 暂停（等本轮工具调用结束后暂停），现在AI的输出速度太快了，得加这个功能，方便调试

# 文档：
- AGENTS.md 大致讲述项目的结构。
- docs/feature-decisions 产品功能决策
- docs/draft-plans 我自己写的初步计划
- docs/plans AI基于初步计划制定的计划
- docs/code_explanations 让 AI 给我解释的一些代码，对其他人应该没啥用。用 [structured-knowledge](https://github.com/jenglong1899/structured-knowledge) skill 制作

你可能需要把 AGENTS.md 中的 `# 用户开发环境` 一节给删掉

# 启动

默认用codex订阅，如果要用其他，需要设置环境变量
```
cd backend
cp .env.example .env
```

Linux/macOS:

```
chmod +x dev.sh
./dev.sh
```

如果用Codex订阅，想在Pycharm中跑断点、codex订阅的集成测试，要在 run configuration 里面加环境变量，key是`PROJECT_X_CODEX_HTTP_PROXY`，value是`socks5h://172.17.16.1:7890`（如果你的vpn是7890端口）
