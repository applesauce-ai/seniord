from __future__ import annotations

from app.db.pool import transaction
from app.repositories import order_repository as orders
from app.repositories import outbox_repository as outbox
from app.repositories import product_repository as products
from app.schemas.orders import CreateOrderRequest, CreateOrderResponse, OrderDetail


class UnknownSkuError(Exception):
    """Raised when an order references a SKU that isn't in the catalogue."""

    def __init__(self, skus: list[str]) -> None:
        self.skus = skus
        super().__init__(f"Unknown SKU(s): {', '.join(skus)}")


class InsufficientStockError(Exception):
    """Raised when an order can't be fully reserved (oversell prevention)."""

    def __init__(self, skus: list[str]) -> None:
        self.skus = skus
        super().__init__(f"Stock unavailable for: {', '.join(skus)}")


def create_order(req: CreateOrderRequest) -> tuple[CreateOrderResponse, bool]:
    """Create an order idempotently.

    Returns (response, created). `created` is True for a first-time insert
    (HTTP 201) and False when the order_ref already existed (HTTP 200), so a
    duplicate submission never creates a second order, items, or stock event.

    Order, items, and the outbox event all commit in one transaction, so an
    accepted order can never exist without its stock event, and vice versa.
    """
    skus = [item.sku for item in req.items]

    with transaction() as conn:
        prices = products.get_prices_by_skus(conn, skus)
        missing = sorted({sku for sku in skus if sku not in prices})
        if missing:
            # Rejected before any write; the transaction rolls back cleanly.
            raise UnknownSkuError(missing)

        # Snapshot prices and compute totals from the catalogue as it is now.
        lines = []
        total_cents = 0
        for item in req.items:
            unit = prices[item.sku]
            line_total = unit * item.qty
            total_cents += line_total
            lines.append((item.sku, item.qty, unit, line_total))

        order_id = orders.insert_order_if_new(
            conn, req.order_ref, req.customer_id, total_cents
        )

        if order_id is None:
            # Duplicate order_ref: return the order that already exists, untouched.
            # Done BEFORE reserving so a resubmission never reserves stock twice.
            existing = orders.get_order_detail(conn, req.order_ref)
            return (
                CreateOrderResponse(
                    order_ref=req.order_ref,
                    status=existing["status"],
                    total_cents=existing["total_cents"],
                    duplicate=True,
                ),
                False,
            )

        # Reserve stock atomically. If any line can't be reserved, raise — the
        # whole transaction rolls back (order + any earlier reservations undone),
        # so an under-stocked order creates nothing and the customer is rejected.
        for sku, qty, _, _ in lines:
            if not products.reserve(conn, sku, qty):
                raise InsufficientStockError([sku])

        for sku, qty, unit, line_total in lines:
            orders.insert_order_item(conn, order_id, sku, qty, unit, line_total)

        outbox.insert_event(
            conn,
            event_type="order.accepted",
            aggregate_id=req.order_ref,
            payload={
                "order_ref": req.order_ref,
                "customer_id": req.customer_id,
                "items": [{"sku": sku, "qty": qty} for sku, qty, _, _ in lines],
            },
        )

        return (
            CreateOrderResponse(
                order_ref=req.order_ref,
                status="accepted",
                total_cents=total_cents,
                duplicate=False,
            ),
            True,
        )


def get_order(order_ref: str) -> OrderDetail | None:
    with transaction() as conn:
        detail = orders.get_order_detail(conn, order_ref)
    return OrderDetail(**detail) if detail is not None else None
