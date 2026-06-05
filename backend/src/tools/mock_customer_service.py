from typing import Any, Literal

from pydantic import BaseModel, Field

from src.tools.tool import Tool


class GetOrderStatusInput(BaseModel):
    order_id: str = Field(min_length=1, description="订单号")


class GetOrderStatusOutput(BaseModel):
    order_id: str
    status: str
    signed_at: str | None


class GetOrderStatusTool:
    _ORDER_STATUS_BY_ID = {
        "ORD-2024": GetOrderStatusOutput(
            order_id="ORD-2024",
            status="已签收",
            signed_at="2026-06-01 18:30:00",
        ),
        "ORD-9999": GetOrderStatusOutput(
            order_id="ORD-9999",
            status="已发货",
            signed_at=None,
        ),
    }

    def to_tool(self) -> Tool:
        return Tool(
            name="get_order_status",
            description="根据订单号查询订单状态，比如物流状态",
            parameters_json_schema=GetOrderStatusInput.model_json_schema(),
            handler=self.run,
        )

    async def run(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_input = GetOrderStatusInput.model_validate(arguments)
        result = self._ORDER_STATUS_BY_ID.get(tool_input.order_id)
        if result is None:
            result = GetOrderStatusOutput(
                order_id=tool_input.order_id,
                status="已发货",
                signed_at=None,
            )
        return result.model_dump()


class CheckRefundPolicyInput(BaseModel):
    category: Literal["电子产品"] = Field(description="商品品类")


class CheckRefundPolicyOutput(BaseModel):
    category: str
    policy: str


class CheckRefundPolicyTool:
    def to_tool(self) -> Tool:
        return Tool(
            name="check_refund_policy",
            description="根据商品品类查询退货政策",
            parameters_json_schema=CheckRefundPolicyInput.model_json_schema(),
            handler=self.run,
        )

    async def run(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_input = CheckRefundPolicyInput.model_validate(arguments)
        return CheckRefundPolicyOutput(
            category=tool_input.category,
            policy=f"{tool_input.category} 7 天可退",
        ).model_dump()


class EscalateToHumanInput(BaseModel):
    reason: str = Field(min_length=1, description="转接人工客服的原因")


class EscalateToHumanOutput(BaseModel):
    reason: str
    escalated: bool


class EscalateToHumanTool:
    def to_tool(self) -> Tool:
        return Tool(
            name="escalate_to_human",
            description="如果用户情绪激动、愤怒，就立刻使用这个工具转接人工客服",
            parameters_json_schema=EscalateToHumanInput.model_json_schema(),
            handler=self.run,
        )

    async def run(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_input = EscalateToHumanInput.model_validate(arguments)
        return EscalateToHumanOutput(
            reason=tool_input.reason,
            escalated=True,
        ).model_dump()


def create_get_order_status_tool() -> Tool:
    return GetOrderStatusTool().to_tool()


def create_check_refund_policy_tool() -> Tool:
    return CheckRefundPolicyTool().to_tool()


def create_escalate_to_human_tool() -> Tool:
    return EscalateToHumanTool().to_tool()
