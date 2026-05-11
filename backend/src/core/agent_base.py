from abc import ABC, abstractmethod
from typing import Any


class AgentBase(ABC):
    @abstractmethod
    def start_conversation(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def enqueue_user_message(self, *, frontend_msg_id: str, user_message: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def request_pause(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def resume(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_paused(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def is_pause_requested(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def run(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def has_pending_work(self) -> bool:
        """
        controller 用它来决定是否需要继续调度 run()。

        语义：只要调用 run() 能推进任何状态（包括执行未完成的 tool、tool 后续 assistant、
        或 drain 排队的 user message），就应该返回 True。
        """
        raise NotImplementedError
