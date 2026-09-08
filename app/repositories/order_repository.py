from __future__ import annotations

from typing import Any

from psycopg import Connection


def insert_order_if_new(
    conn: Connection, order_ref: str, customer_id: str, total_cents: int
) -> int | None:
    """Insert an order, relying on the UNIQUE(order_ref) constraint for idempotency.

    Returns the new order id, or None if an order with this order_ref already
    existed. ON CONFLICT DO NOTHING makes this race-safe: two concurrent
    submissions of the same order_ref, exactly one gets an id back.
    """
    row = conn.execute(
        """
        INSERT INTO orders (order_ref, customer_id, total_cents)
        VALUES (%s, %s, %s)
        ON CONFLICT (order_ref) DO NOTHING
        RETURNING id
        """,
        (order_ref, customer_id, total_cents),
    ).fetchone()
    return row["id"] if row else None


def insert_order_item(
    conn: Connection,
    order_id: int,
    sku: str,
    quantity: int,
    unit_price_cents: int,
    line_total_cents: int,
) -> None:
    conn.execute(
        """
        INSERT INTO order_items
            (order_id, sku, quantity, unit_price_cents, line_total_cents)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (order_id, sku, quantity, unit_price_cents, line_total_cents),
    )


def get_order_detail(conn: Connection, order_ref: str) -> dict[str, Any] | None:
    """Return the order with its items, or None if no such order_ref."""
    order = conn.execute(
        """
        SELECT order_ref, customer_id, status, total_cents
        FROM orders
        WHERE order_ref = %s
        """,
        (order_ref,),
    ).fetchone()
    if order is None:
        return None

    items = conn.execute(
        """
        SELECT i.sku,
               i.quantity AS qty,
               i.unit_price_cents,
               i.line_total_cents
        FROM order_items i
        JOIN orders o ON o.id = i.order_id
        WHERE o.order_ref = %s
        ORDER BY i.id
        """,
        (order_ref,),
    ).fetchall()

    return {**order, "items": items}
