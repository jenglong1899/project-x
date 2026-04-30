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
    async def run(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def has_pending_user_messages(self) -> bool:
        raise NotImplementedError
