# 1
    def on_agent_became_idle(self) -> None:
        self._close_assistant_message()
        self._tool_states.clear()
        self._emit({"type": "agent.became.idle"})
改名字没改全啊，这协议要改，然后前端对应也要改。

# 2
agent.py里面的_noop，还有agent turn.py里面的_noop要改成调用 commmon.py里面的noop。另外是不是应该把common.py合并到utils.py里面？

# 3
_active_assistant_message_id 在前端如何起作用还是没看懂
