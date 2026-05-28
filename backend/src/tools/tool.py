from dataclasses import dataclass
from typing import Any, Protocol


class ToolHandler(Protocol):
    async def __call__(self, *, arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    handler: ToolHandler

    def to_tool_param(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_json_schema,
            },
        }
