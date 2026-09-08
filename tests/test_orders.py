from tests.conftest import count, seed_products


def test_create_order_computes_total_from_catalogue(client):
    seed_products()
    resp = client.post(
        "/orders",
        json={
            "order_ref": "web-1",
            "customer_id": "cust-1",
            "items": [{"sku": "BAN-001", "qty": 2}, {"sku": "APL-003", "qty": 1}],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_cents"] == 2 * 199 + 299  # 697
    assert body["duplicate"] is False

    detail = client.get("/orders/web-1").json()
    assert {i["sku"]: i["line_total_cents"] for i in detail["items"]} == {
        "BAN-001": 398,
        "APL-003": 299,
    }


def test_order_total_uses_snapshot_price(client):
    seed_products()
    client.post(
        "/orders",
        json={"order_ref": "web-1", "customer_id": "c", "items": [{"sku": "BAN-001", "qty": 1}]},
    )

    # Price changes after the order was placed...
    from app.db.pool import transaction
    from app.repositories import product_repository as products

    with transaction() as conn:
        products.upsert_product(conn, "BAN-001", "Bananas 1kg", 999, 50)

    # ...but the stored order keeps the price captured at order time.
    detail = client.get("/orders/web-1").json()
    assert detail["total_cents"] == 199
    assert detail["items"][0]["unit_price_cents"] == 199


def test_unknown_sku_is_rejected_and_writes_nothing(client):
    seed_products()
    resp = client.post(
        "/orders",
        json={"order_ref": "web-bad", "customer_id": "c", "items": [{"sku": "NOPE", "qty": 1}]},
    )
    assert resp.status_code == 400
    assert count("SELECT count(*) FROM orders") == 0
    assert count("SELECT count(*) FROM outbox_events") == 0


def test_get_missing_order_returns_404(client):
    assert client.get("/orders/nope").status_code == 404


def test_invalid_quantity_is_rejected_by_validation(client):
    seed_products()
    resp = client.post(
        "/orders",
        json={"order_ref": "web-z", "customer_id": "c", "items": [{"sku": "BAN-001", "qty": 0}]},
    )
    assert resp.status_code == 422
