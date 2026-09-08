"""Standalone external consumer of the order-accepted feed.

This is what "another system" looks like: it shares NO code with the service —
it depends only on httpx and the feed URL. You could copy this one file into any
other project or machine and it would work. It polls
GET /events/orders?after_id=<cursor>, prints each new event, and persists its
cursor to a file, so it can be stopped at any time and resume exactly where it
left off — it never needs to be online continuously.

Set CONSUMER_CUSTOMER_ID to make it a *filtered* consumer that only receives one
customer's orders (a per-customer subscription).

    python -m scripts.consume_order_events
    # or from anywhere, with just `pip install httpx`:
    API_BASE_URL=http://localhost:8000 python consume_order_events.py

Env:
    API_BASE_URL              base URL of the API (default http://localhost:8000)
    CONSUMER_CUSTOMER_ID      optional: only receive this customer's orders
    CONSUMER_OFFSET_FILE      where to persist the cursor (default ./consumer_offset.txt)
    CONSUMER_POLL_INTERVAL    seconds between polls (default 1.0)
    CONSUMER_RUN_SECONDS      optional: exit after N seconds (used by demos/tests)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
CUSTOMER_ID = os.environ.get("CONSUMER_CUSTOMER_ID", "").strip() or None
OFFSET_FILE = Path(os.environ.get("CONSUMER_OFFSET_FILE", "consumer_offset.txt"))
POLL_INTERVAL = float(os.environ.get("CONSUMER_POLL_INTERVAL", "1.0"))
RUN_SECONDS = float(os.environ.get("CONSUMER_RUN_SECONDS", "0"))  # 0 => run forever


def read_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def write_offset(value: int) -> None:
    OFFSET_FILE.write_text(str(value))


def main() -> None:
    offset = read_offset()
    url = f"{API_BASE_URL}/events/orders"
    deadline = time.monotonic() + RUN_SECONDS if RUN_SECONDS else None

    scope = f" for customer {CUSTOMER_ID}" if CUSTOMER_ID else ""
    print(f"consumer starting from after_id={offset}{scope} (offset file: {OFFSET_FILE})", flush=True)

    with httpx.Client(timeout=5.0) as client:
        while True:
            params = {"after_id": offset, "limit": 100}
            if CUSTOMER_ID:
                params["customer_id"] = CUSTOMER_ID  # filtered subscription
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                events = resp.json()["events"]
            except Exception as exc:  # API restart / transient network blip
                print(f"poll error: {exc}; retrying", flush=True)
            else:
                for event in events:
                    print(
                        f"Received event {event['id']}: "
                        f"{event['type']} {event['order_ref']} @ {event['occurred_at']}",
                        flush=True,
                    )
                    offset = event["id"]
                    write_offset(offset)  # advance only after handling the event

            if deadline is not None and time.monotonic() >= deadline:
                print(f"consumer stopping at after_id={offset}", flush=True)
                return
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
