"""Webhook delivery worker — pushes 'order.accepted' events to a webhook (e.g. Discord).

This is a durable, retrying push adapter and a fully independent component:

  * The API never calls the webhook. Order intake only writes the outbox event
    and returns, so webhook latency/outages cannot affect API performance.
  * Delivery state lives in Postgres (webhook_deliveries), not in this process,
    so the worker can crash/restart/be recreated and resume exactly.
  * Each event is claimed with FOR UPDATE SKIP LOCKED, delivered, then acked in
    the same transaction (at-least-once) — the same pattern as the stock worker.
  * Failures retry with exponential backoff; after MAX_ATTEMPTS the row is
    dead-lettered (status='dead') so one bad endpoint/event never blocks the rest.

    WEBHOOK_URL=<url> python -m scripts.webhook_forwarder

Env:
    WEBHOOK_URL             destination (Discord incoming webhook, or any endpoint). Required.
    WEBHOOK_FORMAT          'discord' (default) sends an embed; 'generic' sends raw event+order JSON.
    WEBHOOK_START           'now' (default) skips history on first init; 'beginning' delivers all.
    WEBHOOK_MAX_ATTEMPTS    dead-letter after this many failed attempts (default 8).
    WEBHOOK_BACKOFF_BASE    base backoff seconds (default 5); doubles per attempt up to a 300s cap.
    WEBHOOK_POLL_INTERVAL   seconds between polls (default 1.0).
    WEBHOOK_RUN_SECONDS     optional: exit after N seconds (used by demos/tests).
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from app.db.pool import apply_schema, close_pool, transaction
from app.repositories import order_repository as orders
from app.repositories import webhook_repository as deliveries

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
WEBHOOK_FORMAT = os.environ.get("WEBHOOK_FORMAT", "discord").strip().lower()
WEBHOOK_START = os.environ.get("WEBHOOK_START", "now").strip().lower()
MAX_ATTEMPTS = int(os.environ.get("WEBHOOK_MAX_ATTEMPTS", "8"))
BACKOFF_BASE = float(os.environ.get("WEBHOOK_BACKOFF_BASE", "5"))
BACKOFF_CAP = 300.0
POLL_INTERVAL = float(os.environ.get("WEBHOOK_POLL_INTERVAL", "1.0"))
RUN_SECONDS = float(os.environ.get("WEBHOOK_RUN_SECONDS", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [webhook] %(message)s")
log = logging.getLogger("webhook_forwarder")


def _money(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


def build_payload(event: dict, order: dict | None) -> dict:
    if WEBHOOK_FORMAT == "generic":
        return {"event": event, "order": order}

    fields = [{"name": "Order", "value": f"`{event['order_ref']}`", "inline": True}]
    if order:
        units = sum(i["qty"] for i in order["items"])
        fields.append({"name": "Customer", "value": f"`{order['customer_id']}`", "inline": True})
        fields.append({"name": "Total", "value": _money(order["total_cents"]), "inline": True})
        item_lines = "\n".join(
            f"`{i['qty']} ×` **{i['sku']}** — {_money(i['line_total_cents'])}"
            for i in order["items"]
        )
        fields.append({"name": f"Items ({units})", "value": item_lines or "—", "inline": False})

    embed = {
        "title": "🛒 Order accepted",
        "color": 0x3FB950,
        "fields": fields,
        "footer": {"text": f"event #{event['id']} · order.accepted"},
        "timestamp": event["occurred_at"],
    }
    return {"username": "Order & Stock System", "embeds": [embed]}


def _backoff_seconds(attempts: int) -> int:
    return int(min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_CAP))


def _deliver_one(client: httpx.Client) -> bool:
    """Claim, deliver, and ack one due event in a single transaction. Returns
    True if a row was processed (so the caller keeps draining), False if none due."""
    with transaction() as conn:
        row = deliveries.claim_one_due(conn)
        if row is None:
            return False

        event_id = row["event_id"]
        order = orders.get_order_detail(conn, row["order_ref"])
        event = {
            "id": event_id,
            "order_ref": row["order_ref"],
            "occurred_at": row["created_at"].isoformat(),
        }
        attempts = row["attempts"] + 1

        error: str | None = None
        try:
            resp = client.post(WEBHOOK_URL, json=build_payload(event, order))
            if resp.status_code >= 300:
                error = f"HTTP {resp.status_code}: {resp.text[:160]}"
        except httpx.HTTPError as exc:
            error = str(exc)

        if error is None:
            deliveries.mark_delivered(conn, event_id, attempts)
            log.info("delivered event %s (%s) after %d attempt(s)", event_id, row["order_ref"], attempts)
        elif attempts >= MAX_ATTEMPTS:
            deliveries.mark_dead(conn, event_id, attempts, error)
            log.error("dead-lettered event %s after %d attempts: %s", event_id, attempts, error)
        else:
            delay = _backoff_seconds(attempts)
            deliveries.mark_retry(conn, event_id, attempts, delay, error)
            log.warning("event %s attempt %d failed (%s); retry in %ds", event_id, attempts, error, delay)
        return True


def main() -> None:
    if not WEBHOOK_URL:
        log.info("WEBHOOK_URL is not set — nothing to forward. See the module docstring.")
        return

    apply_schema()  # self-sufficient: ensure webhook_deliveries exists

    # First-run initialisation: skip existing history unless asked to replay it.
    with transaction() as conn:
        if deliveries.is_empty(conn) and WEBHOOK_START == "now":
            n = deliveries.skip_existing(conn)
            log.info("start=now: skipped %d existing event(s)", n)

    log.info("started (format=%s, max_attempts=%d, base_backoff=%.0fs)", WEBHOOK_FORMAT, MAX_ATTEMPTS, BACKOFF_BASE)
    deadline = time.monotonic() + RUN_SECONDS if RUN_SECONDS else None

    with httpx.Client(timeout=10.0) as client:
        while True:
            try:
                with transaction() as conn:
                    deliveries.enqueue_pending(conn)  # pick up newly-accepted orders
                while _deliver_one(client):
                    pass  # drain everything currently due
            except Exception:  # keep the loop alive; rows stay durable and retry
                log.exception("error in delivery cycle; will retry")

            if deadline is not None and time.monotonic() >= deadline:
                log.info("stopping")
                break
            time.sleep(POLL_INTERVAL)

    close_pool()


if __name__ == "__main__":
    main()
