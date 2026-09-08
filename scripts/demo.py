"""Task 1 demo: seed products and submit a short burst of orders that
intentionally includes duplicate order_refs.

Resets the order tables first so the CREATED/DUPLICATE outcome is deterministic
on every run, then submits the burst against the live API.

    python -m scripts.demo
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.db.pool import close_pool, transaction
from scripts.seed_products import PRODUCTS
from app.repositories import product_repository as products

# (order_ref, [(sku, qty), ...]). Note web-100002 and web-100001 appear twice.
BURST = [
    ("web-100001", [("BAN-001", 2)]),
    ("web-100002", [("APL-003", 1), ("MLK-001", 2)]),
    ("web-100003", [("BAN-001", 1)]),
    ("web-100002", [("APL-003", 1), ("MLK-001", 2)]),  # duplicate
    ("web-100004", [("MLK-001", 3)]),
    ("web-100001", [("BAN-001", 2)]),                  # duplicate
]


def reset_and_seed() -> None:
    with transaction() as conn:
        conn.execute("TRUNCATE order_items, orders, outbox_events CASCADE")
        for sku, name, price_cents, stock in PRODUCTS:
            products.upsert_product(conn, sku, name, price_cents, stock)


def main() -> None:
    print("Resetting order tables and seeding products...\n")
    reset_and_seed()
    close_pool()

    unique_refs: set[str] = set()
    with httpx.Client(base_url=settings.api_base_url, timeout=5.0) as client:
        for order_ref, items in BURST:
            payload = {
                "order_ref": order_ref,
                "customer_id": "cust-demo",
                "items": [{"sku": sku, "qty": qty} for sku, qty in items],
            }
            resp = client.post("/orders", json=payload)
            resp.raise_for_status()
            body = resp.json()
            outcome = "DUPLICATE" if body["duplicate"] else "CREATED"
            if not body["duplicate"]:
                unique_refs.add(order_ref)
            print(
                f"Submitting {order_ref} -> {outcome:<9} "
                f"(HTTP {resp.status_code}, total {body['total_cents']}c)"
            )

    print()
    print(f"Requests submitted: {len(BURST)}")
    print(f"Unique orders:      {len(unique_refs)}")


if __name__ == "__main__":
    main()
