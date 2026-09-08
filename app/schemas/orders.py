from typing import Annotated

from pydantic import BaseModel, Field

# Constrained primitives reused across models.
NonEmptyStr = Annotated[str, Field(min_length=1, max_length=200)]
PositiveInt = Annotated[int, Field(gt=0)]


class OrderItemIn(BaseModel):
    """A single line in a create-order request."""

    sku: NonEmptyStr
    qty: PositiveInt


class CreateOrderRequest(BaseModel):
    order_ref: NonEmptyStr
    customer_id: NonEmptyStr
    items: Annotated[list[OrderItemIn], Field(min_length=1)]


class CreateOrderResponse(BaseModel):
    order_ref: str
    status: str
    total_cents: int
    # True when this request matched an order_ref that already existed, so no
    # new order (and no new stock event) was created.
    duplicate: bool = False


class OrderItemOut(BaseModel):
    sku: str
    qty: int
    unit_price_cents: int
    line_total_cents: int


class OrderDetail(BaseModel):
    order_ref: str
    customer_id: str
    status: str
    total_cents: int
    items: list[OrderItemOut]
