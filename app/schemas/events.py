from datetime import datetime

from pydantic import BaseModel


class OrderEvent(BaseModel):
    """One accepted-order event as exposed on the external feed."""

    id: int
    type: str
    order_ref: str
    occurred_at: datetime


class OrderEventsResponse(BaseModel):
    events: list[OrderEvent]
