"""
兼容层：AgentRunner 已重命名为 AgentController。

说明：
- 现阶段保留该模块，避免外部 import 立刻全量改动
- 新代码应改用 `src.core.agent_controller.AgentController`
"""

from src.core.agent_controller import AgentController as AgentRunner
from src.core.agent_base import AgentBase as RunnableAgent
