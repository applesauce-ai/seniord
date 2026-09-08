from __future__ import annotations

from psycopg import Connection

# All functions take an open connection so they run inside the caller's
# transaction. They never commit — the caller (service / worker) owns that.


def get_prices_by_skus(conn: Connection, skus: list[str]) -> dict[str, int]:
    """Return {sku: price_cents} for the SKUs that exist. Missing SKUs are absent."""
    rows = conn.execute(
        "SELECT sku, price_cents FROM products WHERE sku = ANY(%s)",
        (skus,),
    ).fetchall()
    return {row["sku"]: row["price_cents"] for row in rows}


def get_stock_row(conn: Connection, sku: str) -> dict | None:
    """On-hand and reserved for a SKU, or None if unknown."""
    return conn.execute(
        "SELECT stock, reserved FROM products WHERE sku = %s",
        (sku,),
    ).fetchone()


def list_all(conn: Connection) -> list[dict]:
    """All products (sku, name, price_cents, stock, reserved), ordered by SKU."""
    return conn.execute(
        "SELECT sku, name, price_cents, stock, reserved FROM products ORDER BY sku"
    ).fetchall()


def reserve(conn: Connection, sku: str, qty: int) -> bool:
    """Atomically reserve stock at order time. Returns False if not enough available.

    The `stock - reserved >= qty` guard IS the check, evaluated under the row
    lock this UPDATE takes — so two concurrent reservations for the last units
    serialize and only one can succeed. No app-level check-then-act race.
    """
    cur = conn.execute(
        """
        UPDATE products
        SET reserved = reserved + %s, updated_at = now()
        WHERE sku = %s AND stock - reserved >= %s
        """,
        (qty, sku, qty),
    )
    return cur.rowcount == 1


def settle(conn: Connection, sku: str, qty: int) -> None:
    """Fulfil a reserved order: physical stock leaves and the reservation is released.

    Both drop by the same amount, so `available = stock - reserved` is unchanged
    (it already dropped at reservation time) and the invariant always holds.
    """
    conn.execute(
        """
        UPDATE products
        SET stock = stock - %s, reserved = reserved - %s, updated_at = now()
        WHERE sku = %s
        """,
        (qty, qty, sku),
    )


def set_stock(conn: Connection, sku: str, stock: int) -> None:
    """Set on-hand stock and clear reservations. Used by the console demos."""
    conn.execute(
        "UPDATE products SET stock = %s, reserved = 0, updated_at = now() WHERE sku = %s",
        (stock, sku),
    )


def create_product(
    conn: Connection, sku: str, name: str, price_cents: int, stock: int
) -> bool:
    """Insert a new product. Returns False if the SKU already exists (no change)."""
    cur = conn.execute(
        """
        INSERT INTO products (sku, name, price_cents, stock)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (sku) DO NOTHING
        """,
        (sku, name, price_cents, stock),
    )
    return cur.rowcount == 1


def upsert_product(
    conn: Connection, sku: str, name: str, price_cents: int, stock: int
) -> None:
    """Insert or replace a product. Used by the seed script."""
    conn.execute(
        """
        INSERT INTO products (sku, name, price_cents, stock)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (sku) DO UPDATE
            SET name = EXCLUDED.name,
                price_cents = EXCLUDED.price_cents,
                stock = EXCLUDED.stock,
                reserved = 0,
                updated_at = now()
        """,
        (sku, name, price_cents, stock),
    )
