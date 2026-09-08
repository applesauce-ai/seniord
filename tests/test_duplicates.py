"""Unhappy path #1 — duplicate submissions of the same order_ref."""

from tests.conftest import count, seed_products

ORDER = {
    "order_ref": "web-100045",
    "customer_id": "cust-42",
    "items": [{"sku": "BAN-001", "qty": 2}, {"sku": "APL-003", "qty": 1}],
}


def test_duplicate_order_ref_counts_once(client):
    seed_products()

    first = client.post("/orders", json=ORDER)
    assert first.status_code == 201
    assert first.json()["duplicate"] is False

    for _ in range(2):
        again = client.post("/orders", json=ORDER)
        assert again.status_code == 200
        assert again.json()["duplicate"] is True
        # Duplicate returns the same total as the original.
        assert again.json()["total_cents"] == first.json()["total_cents"]

    # Despite three submissions: one order, one set of items, one event.
    assert count("SELECT count(*) FROM orders WHERE order_ref = %s", ("web-100045",)) == 1
    assert count("SELECT count(*) FROM order_items") == 2
    assert count("SELECT count(*) FROM outbox_events") == 1
