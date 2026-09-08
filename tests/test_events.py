"""The order-accepted feed: ordering, the after_id cursor, and per-customer filtering."""

from tests.conftest import seed_products


def _order(client, ref, customer):
    client.post(
        "/orders",
        json={"order_ref": ref, "customer_id": customer, "items": [{"sku": "BAN-001", "qty": 1}]},
    )


def test_feed_returns_accepted_events_in_order(client):
    seed_products()
    _order(client, "a", "cust-1")
    _order(client, "b", "cust-2")
    events = client.get("/events/orders?after_id=0").json()["events"]
    assert [e["order_ref"] for e in events] == ["a", "b"]


def test_feed_after_id_is_a_cursor(client):
    seed_products()
    _order(client, "a", "cust-1")
    _order(client, "b", "cust-1")
    first_id = client.get("/events/orders?after_id=0").json()["events"][0]["id"]
    rest = client.get(f"/events/orders?after_id={first_id}").json()["events"]
    assert [e["order_ref"] for e in rest] == ["b"]


def test_feed_filters_by_customer(client):
    seed_products()
    _order(client, "a", "cust-1")
    _order(client, "b", "cust-2")
    _order(client, "c", "cust-1")
    events = client.get("/events/orders?after_id=0&customer_id=cust-1").json()["events"]
    assert [e["order_ref"] for e in events] == ["a", "c"]  # cust-2's order is excluded
