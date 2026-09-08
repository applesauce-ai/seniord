from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb


def insert_event(
    conn: Connection, event_type: str, aggregate_id: str, payload: dict[str, Any]
) -> int:
    """Append an outbox event. Called in the same transaction as the order write."""
    row = conn.execute(
        """
        INSERT INTO outbox_events (event_type, aggregate_id, payload)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (event_type, aggregate_id, Jsonb(payload)),
    ).fetchone()
    return row["id"]


def fetch_pending(conn: Connection, limit: int) -> list[dict[str, Any]]:
    """Claim up to `limit` unprocessed events for this worker.

    FOR UPDATE SKIP LOCKED locks each returned row for the life of the
    transaction and lets other workers skip them — so this is safe to run from
    more than one worker process without double-processing.
    """
    return conn.execute(
        """
        SELECT id, aggregate_id, payload
        FROM outbox_events
        WHERE processed_at IS NULL
        ORDER BY id
        FOR UPDATE SKIP LOCKED
        LIMIT %s
        """,
        (limit,),
    ).fetchall()


def counts(conn: Connection) -> dict[str, int]:
    """Pending vs processed event counts (used by the live console monitor)."""
    row = conn.execute(
        """
        SELECT
            count(*) FILTER (WHERE processed_at IS NULL)     AS pending,
            count(*) FILTER (WHERE processed_at IS NOT NULL) AS processed
        FROM outbox_events
        """
    ).fetchone()
    return {"pending": row["pending"], "processed": row["processed"]}


def mark_processed(conn: Connection, event_id: int) -> None:
    conn.execute(
        "UPDATE outbox_events SET processed_at = now() WHERE id = %s",
        (event_id,),
    )


def fetch_accepted_after(
    conn: Connection, after_id: int, limit: int, customer_id: str | None = None
) -> list[dict[str, Any]]:
    """The external feed: accepted-order events with id greater than `after_id`.

    Ordered by id so a consumer can page forward with a simple cursor and replay
    from any point. When `customer_id` is given, only that customer's orders are
    returned — a filtered subscription. The cursor is still the global event id,
    so a filtered consumer just skips the ids that aren't its own.
    """
    return conn.execute(
        """
        SELECT id, event_type, aggregate_id, created_at
        FROM outbox_events
        WHERE id > %(after_id)s
          AND event_type = 'order.accepted'
          AND (%(customer_id)s::text IS NULL OR payload ->> 'customer_id' = %(customer_id)s)
        ORDER BY id
        LIMIT %(limit)s
        """,
        {"after_id": after_id, "limit": limit, "customer_id": customer_id},
    ).fetchall()
