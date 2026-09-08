from __future__ import annotations

from app.db.pool import transaction
from app.repositories import outbox_repository as outbox
from app.repositories import product_repository as products


def process_available(batch_size: int) -> int:
    """Drain all currently-pending outbox events. Returns how many were processed.

    Events are claimed and applied in batches; each batch's stock reductions and
    the matching processed_at updates commit in ONE transaction. So the ack and
    the stock change are inseparable: a crash mid-batch rolls the whole batch
    back and it stays pending for the next cycle (at-least-once).
    """
    total = 0
    while True:
        with transaction() as conn:
            events = outbox.fetch_pending(conn, batch_size)
            if not events:
                break
            for event in events:
                for item in event["payload"]["items"]:
                    products.settle(conn, item["sku"], item["qty"])
                outbox.mark_processed(conn, event["id"])
        total += len(events)
    return total
