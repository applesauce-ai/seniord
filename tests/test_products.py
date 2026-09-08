"""Product listing (availability only) and stock add/update."""

from tests.conftest import seed_products


def test_list_products_shows_availability_not_count(client):
    seed_products()
    items = client.get("/products").json()
    ban = next(p for p in items if p["sku"] == "BAN-001")
    assert ban["in_stock"] is True
    # The remaining count is deliberately NOT exposed on the list.
    assert "available" not in ban and "stock" not in ban and "reserved" not in ban

    client.patch("/products/BAN-001/stock", json={"set": 0})
    items = {p["sku"]: p["in_stock"] for p in client.get("/products").json()}
    assert items["BAN-001"] is False


def test_add_and_set_stock(client):
    seed_products()  # BAN-001 = 50
    assert client.patch("/products/BAN-001/stock", json={"add": 10}).json()["on_hand"] == 60
    assert client.patch("/products/BAN-001/stock", json={"set": 5}).json()["on_hand"] == 5


def test_update_stock_unknown_sku_404(client):
    assert client.patch("/products/NOPE/stock", json={"set": 1}).status_code == 404


def test_update_requires_exactly_one_of_set_or_add(client):
    seed_products()
    assert client.patch("/products/BAN-001/stock", json={}).status_code == 422
    assert client.patch("/products/BAN-001/stock", json={"set": 1, "add": 1}).status_code == 422


def test_cannot_set_stock_below_reserved(client):
    seed_products()
    client.post(
        "/orders",
        json={"order_ref": "r1", "customer_id": "c", "items": [{"sku": "BAN-001", "qty": 10}]},
    )  # reserves 10
    assert client.patch("/products/BAN-001/stock", json={"set": 5}).status_code == 409


def test_create_product_then_list_and_order(client):
    resp = client.post(
        "/products",
        json={"sku": "EGG-012", "name": "Eggs 12pk", "price_cents": 349, "stock": 5},
    )
    assert resp.status_code == 201
    assert resp.json()["available"] == 5

    skus = [p["sku"] for p in client.get("/products").json()]
    assert "EGG-012" in skus

    # The new product is immediately orderable.
    order = client.post(
        "/orders",
        json={"order_ref": "egg-1", "customer_id": "c", "items": [{"sku": "EGG-012", "qty": 2}]},
    )
    assert order.status_code == 201


def test_create_duplicate_product_rejected(client):
    seed_products()  # BAN-001 already exists
    resp = client.post(
        "/products",
        json={"sku": "BAN-001", "name": "Dup", "price_cents": 1, "stock": 1},
    )
    assert resp.status_code == 409


def test_order_at_zero_stock_reports_unavailable(client):
    seed_products()
    client.patch("/products/BAN-001/stock", json={"set": 0})
    resp = client.post(
        "/orders",
        json={"order_ref": "z", "customer_id": "c", "items": [{"sku": "BAN-001", "qty": 1}]},
    )
    assert resp.status_code == 409
    assert "unavailable" in resp.json()["detail"].lower()
