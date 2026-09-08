"""Seed a few sample products. Idempotent (upsert), safe to run repeatedly."""

from __future__ import annotations

from app.db.pool import close_pool, transaction
from app.repositories import product_repository as products

PRODUCTS = [
    # (sku, name, price_cents, stock)
    ("BAN-001", "Bananas 1kg", 199, 50),
    ("APL-003", "Apples 1kg", 299, 30),
    ("MLK-001", "Milk 1L", 189, 20),
]


def main() -> None:
    with transaction() as conn:
        for sku, name, price_cents, stock in PRODUCTS:
            products.upsert_product(conn, sku, name, price_cents, stock)

    print(f"Seeded {len(PRODUCTS)} products:")
    for sku, name, price_cents, stock in PRODUCTS:
        print(f"  {sku}  {name:<14} {price_cents:>4}c  stock={stock}")

    close_pool()


if __name__ == "__main__":
    main()
