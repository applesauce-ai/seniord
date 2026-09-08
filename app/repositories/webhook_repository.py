from __future__ import annotations

from typing import Any

from psycopg import Connection

# Delivery-state access for the webhook push adapter. Like the other repos,
# these take a connection and never commit — the forwarder owns the transaction.


def is_empty(conn: Connection) -> bool:
    return conn.execute(
        "SELECT NOT EXISTS (SELECT 1 FROM webhook_deliveries) AS empty"
    ).fetchone()["empty"]


def skip_existing(conn: Connection) -> int:
    """Mark all current accepted events as 'skipped' (used for start-from-now)."""
    cur = conn.execute(
        """
        INSERT INTO webhook_deliveries (event_id, status, delivered_at)
        SELECT id, 'skipped', now()
        FROM outbox_events
        WHERE event_type = 'order.accepted'
        ON CONFLICT (event_id) DO NOTHING
        """
    )
    return cur.rowcount


def enqueue_pending(conn: Connection) -> int:
    """Create a pending delivery row for any accepted event that doesn't have one."""
    cur = conn.execute(
        """
        INSERT INTO webhook_deliveries (event_id)
        SELECT e.id
        FROM outbox_events e
        WHERE e.event_type = 'order.accepted'
          AND NOT EXISTS (SELECT 1 FROM webhook_deliveries d WHERE d.event_id = e.id)
        ON CONFLICT (event_id) DO NOTHING
        """
    )
    return cur.rowcount


def claim_one_due(conn: Connection) -> dict[str, Any] | None:
    """Lock and return the next due delivery (oldest first), or None.

    FOR UPDATE ... SKIP LOCKED means multiple forwarders can run without ever
    delivering the same event twice concurrently.
    """
    return conn.execute(
        """
        SELECT d.event_id, d.attempts, e.aggregate_id AS order_ref, e.created_at
        FROM webhook_deliveries d
        JOIN outbox_events e ON e.id = d.event_id
        WHERE d.status = 'pending' AND d.next_attempt_at <= now()
        ORDER BY d.event_id
        FOR UPDATE OF d SKIP LOCKED
        LIMIT 1
        """
    ).fetchone()


def mark_delivered(conn: Connection, event_id: int, attempts: int) -> None:
    conn.execute(
        """
        UPDATE webhook_deliveries
        SET status = 'delivered', attempts = %s, delivered_at = now(),
            last_error = NULL, updated_at = now()
        WHERE event_id = %s
        """,
        (attempts, event_id),
    )


def mark_retry(
    conn: Connection, event_id: int, attempts: int, delay_seconds: int, error: str
) -> None:
    conn.execute(
        """
        UPDATE webhook_deliveries
        SET attempts = %s,
            next_attempt_at = now() + make_interval(secs => %s),
            last_error = %s, updated_at = now()
        WHERE event_id = %s
        """,
        (attempts, delay_seconds, error[:500], event_id),
    )


def mark_dead(conn: Connection, event_id: int, attempts: int, error: str) -> None:
    conn.execute(
        """
        UPDATE webhook_deliveries
        SET status = 'dead', attempts = %s, last_error = %s, updated_at = now()
        WHERE event_id = %s
        """,
        (attempts, error[:500], event_id),
    )


def status_counts(conn: Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, count(*) AS n FROM webhook_deliveries GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}
