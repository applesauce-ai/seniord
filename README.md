# Order & Stock System

A minimal, production-minded order-intake and stock-update system in **Python +
PostgreSQL**. Orders are accepted synchronously and idempotently; stock is
updated asynchronously by a background worker driven by a transactional
**outbox**; accepted orders are published on a replayable **event feed** for
external consumers.

The goal is to demonstrate sound engineering decisions — idempotency,
transaction boundaries, eventual consistency, durable recovery — not to build a
full e-commerce platform. Scope is deliberately small.

> Runs locally on **Linux or macOS** via Docker Compose.

📺 **[Tech Assessment Overview Video](https://youtu.be/OivLB2jD3D8)** — a short walkthrough of the working system and design.

---

## Architecture

```text
   client
     │  POST /orders
     ▼
┌─────────────┐      one transaction        ┌──────────────┐
│  FastAPI    │  order + items + event ───▶  │  PostgreSQL  │
│  (api)      │                              │              │
│             │  GET /orders /products       │  products    │
│             │      /events/orders          │  orders      │
└─────────────┘                              │  order_items │
                                             │  outbox_events│
┌─────────────┐   poll pending events        └──────┬───────┘
│ Stock       │  ◀───────────────────────────────── │
│ Worker      │   reduce stock + ack (one txn)       │
│ (worker)    │ ────────────────────────────────────┘
└─────────────┘

external consumer  ──▶  GET /events/orders?after_id=<cursor>  (replayable)
```

Two independent processes (separate Compose services) share one PostgreSQL,
which is the single source of truth:

- **api** — accepts orders, serves reads and the event feed.
- **worker** — drains the outbox and applies stock changes.

The API never waits for the worker. An accepted order and its stock update are
**eventually consistent**, connected by a durable outbox event.

---

## Tech stack

| Concern      | Choice                                   |
| ------------ | ---------------------------------------- |
| Language     | Python 3.12                              |
| Web          | FastAPI + Uvicorn                        |
| Database     | PostgreSQL 16                            |
| DB access    | psycopg 3 (raw SQL, no ORM)              |
| Validation   | Pydantic v2                              |
| Tests        | pytest + FastAPI TestClient              |
| Run          | Docker Compose                           |

**Why raw psycopg3 over an ORM + migrations:** the exercise is about
transactional correctness, and raw SQL keeps the transaction boundaries visible
in the code. The schema is four fixed tables with no migration history, so a
single [`app/db/schema.sql`](app/db/schema.sql) applied on startup is simpler
than Alembic. An ORM + migrations would be the right call once the domain grows
past a handful of tables.

---

## Quick start

Prerequisites: Docker + Docker Compose, on Linux or macOS.

```bash
cp .env.example .env
make up        # build + start db, api, worker
make seed      # seed sample products (BAN-001, APL-003, MLK-001)
make demo      # submit a burst of orders including duplicates
```

- **Live console:** http://localhost:8000/console — a dark, self-contained page
  (served by the API) with a live monitor (orders / pending / processed events, a
  pending-trend sparkline, per-SKU stock bars), a **traffic generator** (~10
  orders/sec, start/stop), an API playground, and a duplicate-burst runner. Start
  the traffic, stop the worker in your terminal, and watch pending climb while
  stock holds — then restart the worker and watch it catch up.
- **API docs (Swagger UI):** http://localhost:8000/docs

To inspect the database with an external client (pgAdmin, DBeaver, psql),
connect to **host port 5433** — user `orders`, password `orders`, database
`orders`. (The container publishes on 5433 to avoid clashing with a local
PostgreSQL already using 5432; container-internal connections still use 5432.)

Every `make` target maps to a plain `docker compose ...` command (see the
[Makefile](Makefile)) if you'd rather not use make.

| Command            | What it does                                          |
| ------------------ | ----------------------------------------------------- |
| `make up`          | Build and start `db` + `api` + `worker`               |
| `make down`        | Stop everything and drop the database volume          |
| `make logs`        | Tail service logs                                     |
| `make seed`        | Seed sample products                                  |
| `make demo`        | Reset orders, seed, submit a burst with duplicates    |
| `make outage-demo` | Worker outage + catch-up demonstration                |
| `make consume`     | Run the consumer inline (foreground; dies if the API container stops) |
| `make consumer-up` | Run the consumer as its own container (survives the API stopping)     |
| `make webhook`     | Start the Discord/webhook forwarder (needs `WEBHOOK_URL` in `.env`) |
| `make test`        | Run the test suite (isolated `orders_test` database)  |

A Postman collection covering every endpoint is in
[`postman_collection.json`](postman_collection.json).

---

## API reference

### `POST /orders` — create an order (idempotent)

```json
{
  "order_ref": "web-100045",
  "customer_id": "cust-42",
  "items": [{"sku": "BAN-001", "qty": 2}, {"sku": "APL-003", "qty": 1}]
}
```

- **201 Created** on first submission → `{"order_ref": "...", "status": "accepted", "total_cents": 697, "duplicate": false}`
- **200 OK** on a repeat of the same `order_ref` → same body with `"duplicate": true`, no new order/stock event
- **400** if an item references an unknown SKU (nothing is written)
- **409 Conflict** if stock can't be reserved for every line — the order is rejected and nothing is written (see *Oversell prevention* below)
- **422** if the payload is invalid (empty items, `qty <= 0`, …)

### `GET /orders/{order_ref}` — order details

Returns the order with its line items, each carrying the **price snapshot** taken
at order time. **404** if unknown.

### `GET /products` — catalogue

Lists products with an **availability boolean**, deliberately *not* the remaining
count:

```json
[{"sku": "BAN-001", "name": "Bananas 1kg", "price_cents": 199, "in_stock": true}]
```

### `POST /products` — create a new product

```json
{"sku": "EGG-012", "name": "Eggs 12pk", "price_cents": 349, "stock": 40}
```

**201** with the created product's levels; **409** if the SKU already exists.

### `GET /products/{sku}/stock` — detailed stock levels

```json
{"sku": "BAN-001", "available": 44, "on_hand": 50, "reserved": 6}
```

`available = on_hand − reserved` — what a new order can take. **404** if the SKU
is unknown.

### `PATCH /products/{sku}/stock` — add or set stock

Body carries exactly one of `add` (delta) or `set` (absolute on-hand):

```json
{"add": 20}      // or {"set": 100}
```

Returns the new levels. **409** if the change would drop on-hand below what's
reserved; **404** if unknown; **422** if neither/both fields are given.

### `GET /events/orders?after_id=<id>&limit=<n>&customer_id=<id>` — accepted-order feed

Replayable, ordered feed of `order.accepted` events with `id` greater than
`after_id`. The event `id` is the cursor a consumer pages forward with. The
optional `customer_id` narrows the feed to a single customer's orders (a filtered
subscription) — the cursor stays the global event id, so a filtered consumer just
skips the ids that aren't its own.

```json
{"events": [{"id": 101, "type": "order.accepted", "order_ref": "web-100045", "occurred_at": "2026-09-07T08:15:00Z"}]}
```

> Note: `customer_id` here is a *filter*, not authorization — anyone could pass
> any id. Making it "only your own orders" in a trustworthy way needs auth (the
> server derives the allowed customer from an authenticated identity), which is
> out of scope for this exercise.

### `GET /health`

`{"status": "ok"}` — also pings the database.

---

## Data model

| Table           | Purpose                                                              |
| --------------- | ------------------------------------------------------------------- |
| `products`      | SKU, name, `price_cents`, `stock` (on-hand), `reserved`; `available = stock − reserved` |
| `orders`        | `order_ref` **UNIQUE**, `customer_id`, `total_cents`, `status`      |
| `order_items`   | line items with `unit_price_cents` / `line_total_cents` (snapshot)  |
| `outbox_events` | `order.accepted` events; `processed_at IS NULL` ⇒ pending           |

See [`app/db/schema.sql`](app/db/schema.sql). Applied automatically on API
startup (idempotent) — no manual migration step.

---

## Design decisions

**Idempotency via a database constraint.** `orders.order_ref` is `UNIQUE`, and
inserts use `INSERT ... ON CONFLICT (order_ref) DO NOTHING RETURNING id`. A real
insert returns an id; a duplicate returns nothing, and the service returns the
existing order. This is race-safe in a way an application-level "does it exist?"
check is not: two concurrent identical submissions, the database guarantees
exactly one wins.

**Transactional outbox.** The order, its items, and the `order.accepted` event
are written in **one transaction**. So an accepted order can never exist without
its stock event, and vice versa — no lost events, no phantom orders.

**Oversell prevention — reserve at checkout, settle asynchronously.** Stock is
split into `stock` (on-hand) and `reserved`, with `available = stock − reserved`.
Order intake **atomically reserves** each line:

```sql
UPDATE products SET reserved = reserved + :qty
WHERE sku = :sku AND stock - reserved >= :qty
```

The `stock - reserved >= qty` guard is evaluated under the row lock this UPDATE
takes, so two concurrent orders for the last units **serialise** — one reserves,
the other re-checks the guard, fails (0 rows), and the order is rejected with
**409**. No app-level check-then-act race, no overselling. A multi-item order
reserves all lines in one transaction: if any line is short, the whole order
rolls back (all-or-nothing). The worker later **settles** delivered orders
(`stock -= qty, reserved -= qty`), so physical stock catches up asynchronously
while `available` was already correct the instant the order was accepted. A DB
`CHECK (stock >= reserved)` enforces the invariant as a backstop.

**Eventual consistency.** The API reserves and responds without waiting for the
worker to settle. Physical `stock` reflects the order shortly after, once the
worker processes the event; `available` reflects it immediately. This keeps the
intake path fast while still preventing oversell.

**Worker correctness.** The worker claims pending events with
`SELECT ... FOR UPDATE SKIP LOCKED` (safe to run as multiple workers) and applies
the stock reduction and the `processed_at` ack in the **same transaction**. A
crash mid-batch rolls back and the events stay pending — at-least-once,
restart-safe.

**Replayable feed.** The event feed uses an `id` cursor, not a live connection.
Consumers store their own offset and can go offline and catch up. Stock
processing and external consumption are independent readers of the same log. The
consumer is a fully independent process — run it as its own container with
`make consumer-up` (its cursor persists in a volume) and it keeps running when
the API is stopped, retrying and catching up when it returns. To make it survive
`docker compose down` entirely, run it on the host instead:
`API_BASE_URL=http://localhost:8000 python -m scripts.consume_order_events`
(needs `pip install httpx pydantic-settings`). Event ids are monotonic (resets
never rewind them), so a saved cursor always points at real, still-unseen events.

**Push built on the outbox, without touching the API.** The optional webhook
forwarder ([scripts/webhook_forwarder.py](scripts/webhook_forwarder.py)) is a
second, independent consumer of the outbox — the push variant of Option A (the
polling feed remains the pull variant and the source of truth). Key properties,
because these are exactly what a webhook needs to get right:

- **Never on the request path.** Order intake only writes the outbox event and
  returns; the API never calls the webhook, so webhook latency or outages can't
  affect API performance.
- **Durable delivery state.** Each accepted event gets a row in a
  `webhook_deliveries` table (`status`, `attempts`, `next_attempt_at`,
  `delivered_at`, `last_error`). State lives in Postgres, not in the process, so
  the forwarder can crash/restart/be recreated and resume exactly.
- **Same claim-process-ack pattern as the stock worker.** It claims a due row
  with `FOR UPDATE SKIP LOCKED`, POSTs, then acks in the same transaction —
  at-least-once, multi-worker safe.
- **Retry with backoff + dead-letter.** A failed delivery is retried with
  exponential backoff; after `WEBHOOK_MAX_ATTEMPTS` the row is dead-lettered
  (`status='dead'`) so one bad endpoint or event never blocks or spams the rest.

Enable it by putting a `WEBHOOK_URL` in `.env` (never commit it) and running
`make webhook`. It starts in `WEBHOOK_START=now` mode, so it doesn't replay
history into the channel.

**Independent components.** api and worker are separate processes sharing only
PostgreSQL; either can be restarted, scaled, or replaced without the other.

**Scaling the feed (a known limit of polling).** The consumer polls once a
second, which is fine here but wouldn't scale: polling load is proportional to
*number of consumers × poll frequency*, not to how many orders actually happen.
Concretely — 11,000 shops each polling every second is ~11,000 requests a second,
nearly all returning nothing, whether there are 11,000 orders that second or none.
It would hammer the API for no reason — a self-inflicted "thundering herd" (not a
DDoS, but the same overload effect). At real scale you flip pull to **push** so
load tracks *orders* instead: that's exactly what the webhook adapter is (a client
only hears from us when there's genuinely an order for it). The other standard
answers are **long-polling** (hold the request open until there's data), a
**message broker** (Kafka/RabbitMQ/SNS-SQS — clients subscribe), or a **live
connection per client** (WebSocket/SSE — how a restaurant tablet gets its "ding").
If polling had to stay, you'd add per-client rate limits, jittered intervals to
avoid a synchronised spike, and serve the feed from a read replica or cache.

**Per-shop webhooks (how the push side scales).** The natural production shape is
the standard SaaS pattern (Stripe/Shopify/Twilio): a shop registers a webhook URL
when it onboards, and we POST its orders to it. That's the forwarder in this repo,
generalised from one hardcoded URL to a `webhook_subscriptions` table
(`shop_id`, `target_url`, `secret`, `active`). On `order.accepted` the dispatcher
looks up the subscription(s) for that order's shop and enqueues a delivery to each,
reusing the same `webhook_deliveries` retry/backoff/dead-letter machinery. The
pieces that make it production-grade:

- **Signed payloads** — an HMAC signature header (per-shop `secret`) so the shop
  can verify the call is genuinely from us; HTTPS always.
- **Idempotent receivers** — delivery is at-least-once, so shops dedupe on the
  event id (they will occasionally see a repeat).
- **Failure handling** — transient errors retry with backoff; persistent ones
  dead-letter, alert the shop, and can auto-disable the subscription. Because the
  feed is replayable, a "replay failed deliveries" button is straightforward.
- **A pull fallback** — not every small shop can host an HTTPS endpoint, so the
  polling feed (or a device/app holding a live connection — the restaurant
  "ding") stays as the alternative. That's the honest reason to keep both push
  and pull rather than only one.

---

## The two unhappy paths

### 1. Duplicate submissions

```bash
make demo
```

Submits a burst where `web-100001` and `web-100002` each appear twice. Output
shows `CREATED` vs `DUPLICATE`; the database ends with one order, one set of
items, and one event per unique `order_ref`.

### 2. Worker outage + catch-up

```bash
make outage-demo
```

Stops the worker, submits orders (still accepted, **201**), shows stock frozen
and events pending, then resumes the worker and shows stock catching up. E.g.
BAN-001 starts at 50, three orders totalling 6 units arrive during the outage,
and after resume stock settles at 44 with zero pending events.

---

## Testing

```bash
make test
```

Integration tests run against a dedicated `orders_test` database (so they never
collide with the running worker or your dev data) and cover both unhappy paths
plus order creation, price-snapshot behaviour, validation, and stock catch-up.

---

## Out of scope / known limitations

Deliberately excluded to keep the exercise focused; each is a reasonable next
step, not an oversight:

- **No auth**, payments, carts, refunds, discounts, or customer management.
- **Catalogue management is unauthenticated.** Creating products (`POST /products`),
  restocking / setting stock (`PATCH /products/{sku}/stock`), and the console's
  demo aids (`/console/*`) are admin-style operations with no authentication. In
  production these would sit behind an admin role / API key; they're open here to
  keep the exercise focused (consistent with the no-auth scope).
- **Reservation is permanent until settled** — there's no reservation TTL /
  expiry (an abandoned reservation isn't released) and no backorders or partial
  fulfilment. Overselling itself *is* prevented (see *Oversell prevention*).
- **Same `order_ref`, different body** — the first stored order wins; a
  conflicting resubmission is ignored (`order_ref` is the identity).
- **No message broker** (Kafka/RabbitMQ), no orchestration, no auth on the feed.

Future evolution would introduce these as scale and operational needs justify:
a broker or CDC in place of polling, per-service databases, dead-letter queues,
retry/backoff policies, event versioning, and observability.
