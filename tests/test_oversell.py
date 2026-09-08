"""Oversell prevention — an order that can't be fully reserved is rejected (409),
and two concurrent orders for the last units can't both win."""

import psycopg

from app.config import settings
from app.db.pool import transaction
from app.repositories import product_repository as products
from tests.conftest import count, seed_products


def _set_stock(sku: str, n: int) -> None:
    with transaction() as conn:
        products.set_stock(conn, sku, n)


def test_order_rejected_when_insufficient_stock(client):
    seed_products()
    _set_stock("BAN-001", 1)

    resp = client.post(
        "/orders",
        json={"order_ref": "web-x", "customer_id": "c", "items": [{"sku": "BAN-001", "qty": 2}]},
    )
    assert resp.status_code == 409

    # Nothing was written and nothing was reserved.
    assert count("SELECT count(*) FROM orders") == 0
    assert count("SELECT count(*) FROM outbox_events") == 0
    s = client.get("/products/BAN-001/stock").json()
    assert (s["available"], s["reserved"]) == (1, 0)


def test_multi_item_order_is_all_or_nothing(client):
    seed_products()
    _set_stock("BAN-001", 10)
    _set_stock("APL-003", 0)  # this line can't be reserved

    resp = client.post(
        "/orders",
        json={
            "order_ref": "web-y",
            "customer_id": "c",
            "items": [{"sku": "BAN-001", "qty": 1}, {"sku": "APL-003", "qty": 1}],
        },
    )
    assert resp.status_code == 409
    # The BAN-001 line must NOT have been left reserved (whole txn rolled back).
    assert client.get("/products/BAN-001/stock").json()["reserved"] == 0
    assert count("SELECT count(*) FROM orders") == 0


def test_two_concurrent_orders_only_one_wins(client):
    """The last 5 units, two orders of 5. The row lock serialises them: the first
    reservation commits, the second re-evaluates the guard and fails."""
    seed_products()
    _set_stock("BAN-001", 5)

    a = psycopg.connect(settings.database_url)
    b = psycopg.connect(settings.database_url)
    try:
        # A reserves all 5 but hasn't committed yet.
        assert products.reserve(a, "BAN-001", 5) is True
        a.commit()

        # B now tries the same units; guard now fails -> no reservation.
        assert products.reserve(b, "BAN-001", 5) is False
        b.commit()
    finally:
        a.close()
        b.close()

    s = client.get("/products/BAN-001/stock").json()
    assert (s["available"], s["reserved"]) == (0, 5)
