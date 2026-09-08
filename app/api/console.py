"""A local, dark-themed console for demonstrating the system.

Served by the API itself (same origin), so the page makes real calls to the
live endpoints with no CORS configuration. Purely a demo aid — the routes are
hidden from the OpenAPI schema.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db.pool import transaction
from app.repositories import outbox_repository as outbox
from app.repositories import product_repository as products
from scripts.seed_products import PRODUCTS

router = APIRouter(tags=["console"])

_HTML_PATH = Path(__file__).resolve().parent.parent / "static" / "console.html"


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/console")


@router.get("/console", response_class=HTMLResponse, include_in_schema=False)
def console_page() -> HTMLResponse:
    return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))


@router.get("/console/stats", include_in_schema=False)
def console_stats() -> dict:
    with transaction() as conn:
        counts = outbox.counts(conn)
        product_rows = products.list_all(conn)
        order_count = conn.execute("SELECT count(*) AS c FROM orders").fetchone()["c"]
    return {
        "orders": order_count,
        "pending_events": counts["pending"],
        "processed_events": counts["processed"],
        "products": [
            {
                "sku": p["sku"],
                "name": p["name"],
                "on_hand": p["stock"],
                "reserved": p["reserved"],
                "available": p["stock"] - p["reserved"],
            }
            for p in product_rows
        ],
    }


@router.post("/console/set_stock", include_in_schema=False)
def console_set_stock(payload: dict) -> dict:
    """Demo aid: set a SKU's on-hand stock and clear its reservations."""
    with transaction() as conn:
        products.set_stock(conn, payload["sku"], int(payload["stock"]))
    return {"ok": True}


@router.post("/console/reset", include_in_schema=False)
def console_reset() -> dict:
    """Demo aid: clear orders/items/events and re-seed products to their defaults."""
    with transaction() as conn:
        conn.execute(
            "TRUNCATE order_items, orders, outbox_events CASCADE"
        )
        for sku, name, price_cents, stock in PRODUCTS:
            products.upsert_product(conn, sku, name, price_cents, stock)
    return {"status": "reset", "products": len(PRODUCTS)}
