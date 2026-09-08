"""Unhappy path #2 — stock settles/catches up after a worker interruption.

With reserve-at-intake, an accepted order reserves stock immediately (available
drops), while physical on-hand stock is only reduced when the worker settles the
event. The worker's outage is simulated by simply not running it, then invoking
its processing step and asserting the backlog settles.
"""

from app.services import stock_service
from tests.conftest import count, seed_products


def _stock(client, sku: str) -> dict:
    return client.get(f"/products/{sku}/stock").json()


def test_reservation_immediate_settlement_deferred(client):
    seed_products()  # BAN-001 on_hand=50
    client.post(
        "/orders",
        json={"order_ref": "web-1", "customer_id": "c", "items": [{"sku": "BAN-001", "qty": 2}]},
    )

    # Reserved at intake: available drops now, on-hand only after the worker runs.
    s = _stock(client, "BAN-001")
    assert (s["available"], s["on_hand"], s["reserved"]) == (48, 50, 2)
    assert count("SELECT count(*) FROM outbox_events WHERE processed_at IS NULL") == 1

    processed = stock_service.process_available(batch_size=50)
    assert processed == 1
    s = _stock(client, "BAN-001")
    assert (s["available"], s["on_hand"], s["reserved"]) == (48, 48, 0)
    assert count("SELECT count(*) FROM outbox_events WHERE processed_at IS NULL") == 0


def test_worker_catches_up_on_backlog_after_outage(client):
    seed_products()

    # Three orders arrive while the worker is "down" (not settling).
    for ref, qty in [("web-A", 2), ("web-B", 3), ("web-C", 1)]:
        client.post(
            "/orders",
            json={"order_ref": ref, "customer_id": "c", "items": [{"sku": "BAN-001", "qty": qty}]},
        )

    # All accepted + reserved: available 44, on-hand still 50, 3 events pending.
    s = _stock(client, "BAN-001")
    assert (s["available"], s["on_hand"], s["reserved"]) == (44, 50, 6)
    assert count("SELECT count(*) FROM outbox_events WHERE processed_at IS NULL") == 3

    # Worker resumes and settles the whole backlog in one pass.
    processed = stock_service.process_available(batch_size=50)
    assert processed == 3
    s = _stock(client, "BAN-001")
    assert (s["available"], s["on_hand"], s["reserved"]) == (44, 44, 0)
    assert count("SELECT count(*) FROM outbox_events WHERE processed_at IS NULL") == 0
