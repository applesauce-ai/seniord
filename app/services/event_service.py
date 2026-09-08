from __future__ import annotations

from app.db.pool import transaction
from app.repositories import outbox_repository as outbox
from app.schemas.events import OrderEvent


def get_accepted_after(
    after_id: int, limit: int, customer_id: str | None = None
) -> list[OrderEvent]:
    """Accepted-order events with id > after_id, oldest first.

    Reads straight from the outbox and is independent of whether the stock
    worker has processed an event yet — the feed is a separate consumer of the
    same durable log. The event id is the cursor a consumer pages forward with.
    An optional customer_id narrows the feed to one customer's orders.
    """
    with transaction() as conn:
        rows = outbox.fetch_accepted_after(conn, after_id, limit, customer_id)
    return [
        OrderEvent(
            id=row["id"],
            type=row["event_type"],
            order_ref=row["aggregate_id"],
            occurred_at=row["created_at"],
        )
        for row in rows
    ]
