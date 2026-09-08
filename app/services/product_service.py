from __future__ import annotations

from app.db.pool import transaction
from app.repositories import product_repository as products


class ProductExistsError(Exception):
    """Raised when creating a product whose SKU already exists."""

    def __init__(self, sku: str) -> None:
        self.sku = sku
        super().__init__(f"Product already exists: {sku}")


class StockUpdateError(Exception):
    """Raised when a stock update would break the invariants (negative, or below
    what is already reserved)."""

    def __init__(self, sku: str, requested: int, reserved: int) -> None:
        self.sku = sku
        super().__init__(
            f"Cannot set {sku} on-hand to {requested}: must be >= 0 and >= reserved ({reserved})"
        )


def list_products() -> list[dict]:
    """Catalogue listing. Exposes availability as a boolean only — not the count."""
    with transaction() as conn:
        rows = products.list_all(conn)
    return [
        {
            "sku": r["sku"],
            "name": r["name"],
            "price_cents": r["price_cents"],
            "in_stock": (r["stock"] - r["reserved"]) > 0,
        }
        for r in rows
    ]


def create_product(sku: str, name: str, price_cents: int, stock: int) -> dict:
    """Create a new product. Raises ProductExistsError if the SKU is taken."""
    with transaction() as conn:
        if not products.create_product(conn, sku, name, price_cents, stock):
            raise ProductExistsError(sku)
    return {
        "sku": sku,
        "name": name,
        "price_cents": price_cents,
        "on_hand": stock,
        "reserved": 0,
        "available": stock,
    }


def get_stock(sku: str) -> dict | None:
    """On-hand / reserved / available for a SKU, or None if unknown."""
    with transaction() as conn:
        row = products.get_stock_row(conn, sku)
    if row is None:
        return None
    return {
        "on_hand": row["stock"],
        "reserved": row["reserved"],
        "available": row["stock"] - row["reserved"],
    }


def update_stock(sku: str, set_to: int | None = None, add: int | None = None) -> dict | None:
    """Set or add on-hand stock. Returns the new levels, or None if SKU unknown.

    Locks the row so a concurrent order's reservation can't race the update, and
    refuses to drop on-hand below what's already reserved (would break the
    stock >= reserved invariant).
    """
    with transaction() as conn:
        row = conn.execute(
            "SELECT stock, reserved FROM products WHERE sku = %s FOR UPDATE",
            (sku,),
        ).fetchone()
        if row is None:
            return None

        new_on_hand = set_to if set_to is not None else row["stock"] + add
        if new_on_hand < 0 or new_on_hand < row["reserved"]:
            raise StockUpdateError(sku, new_on_hand, row["reserved"])

        conn.execute(
            "UPDATE products SET stock = %s, updated_at = now() WHERE sku = %s",
            (new_on_hand, sku),
        )
        return {
            "on_hand": new_on_hand,
            "reserved": row["reserved"],
            "available": new_on_hand - row["reserved"],
        }
