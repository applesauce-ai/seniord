from fastapi import APIRouter, Query

from app.schemas.events import OrderEventsResponse
from app.services import event_service

router = APIRouter(tags=["events"])


@router.get("/events/orders", response_model=OrderEventsResponse)
def order_events(
    after_id: int = Query(0, ge=0, description="Return events with id greater than this."),
    limit: int = Query(100, ge=1, le=1000),
    customer_id: str | None = Query(
        None, description="Optional: only this customer's accepted orders."
    ),
) -> OrderEventsResponse:
    events = event_service.get_accepted_after(after_id, limit, customer_id)
    return OrderEventsResponse(events=events)
