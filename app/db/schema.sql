-- Single source of truth for the schema. Idempotent: safe to apply on every
-- startup and from scripts/tests. No ORM, no migration tool for this exercise.

CREATE TABLE IF NOT EXISTS products (
    id           SERIAL PRIMARY KEY,
    sku          TEXT        NOT NULL UNIQUE,
    name         TEXT        NOT NULL,
    price_cents  INTEGER     NOT NULL CHECK (price_cents >= 0),
    stock        INTEGER     NOT NULL DEFAULT 0,   -- physical on-hand units
    reserved     INTEGER     NOT NULL DEFAULT 0,   -- committed to accepted, unsettled orders
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- available = stock - reserved. This invariant is what makes overselling
    -- impossible: a reservation can only ever be granted while it holds.
    CONSTRAINT products_stock_ge_reserved CHECK (stock >= reserved)
);

-- Idempotent migration for databases created before `reserved` existed.
ALTER TABLE products ADD COLUMN IF NOT EXISTS reserved INTEGER NOT NULL DEFAULT 0;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'products_stock_ge_reserved') THEN
        ALTER TABLE products ADD CONSTRAINT products_stock_ge_reserved CHECK (stock >= reserved);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS orders (
    id           SERIAL PRIMARY KEY,
    -- Business identity of the order. The UNIQUE constraint is what makes
    -- duplicate submissions idempotent at the database level (not app checks).
    order_ref    TEXT        NOT NULL UNIQUE,
    customer_id  TEXT        NOT NULL,
    total_cents  INTEGER     NOT NULL CHECK (total_cents >= 0),
    status       TEXT        NOT NULL DEFAULT 'accepted',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_items (
    id                SERIAL PRIMARY KEY,
    order_id          INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    sku               TEXT    NOT NULL,
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    -- Price snapshot at order time so later price changes don't rewrite history.
    unit_price_cents  INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    line_total_cents  INTEGER NOT NULL CHECK (line_total_cents >= 0)
);

CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id);

CREATE TABLE IF NOT EXISTS outbox_events (
    id            BIGSERIAL PRIMARY KEY,
    event_type    TEXT        NOT NULL,
    aggregate_id  TEXT        NOT NULL,      -- the order_ref this event is about
    payload       JSONB       NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at  TIMESTAMPTZ                -- NULL => the worker still owes this
);

-- Keeps the worker's "find pending work" poll cheap regardless of table size.
CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox_events (id)
    WHERE processed_at IS NULL;

-- Per-event delivery state for the (optional) webhook push adapter. This is a
-- second, independent consumer of the outbox — separate from stock processing —
-- with its own retry/backoff and dead-letter tracking, so a slow or down webhook
-- endpoint never touches the order-intake path.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    event_id        BIGINT PRIMARY KEY REFERENCES outbox_events (id) ON DELETE CASCADE,
    status          TEXT        NOT NULL DEFAULT 'pending',  -- pending | delivered | dead | skipped
    attempts        INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),      -- when this row is next eligible
    delivered_at    TIMESTAMPTZ,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cheap "what's due for delivery now" lookup.
CREATE INDEX IF NOT EXISTS idx_webhook_due
    ON webhook_deliveries (next_attempt_at)
    WHERE status = 'pending';
