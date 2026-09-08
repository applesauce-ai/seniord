# Order & Stock System — Project Plan

## 1. Project Overview

Build a minimal, production-minded order and stock system in Python with PostgreSQL.

The system should demonstrate:

- Fast API development in Python
- PostgreSQL persistence and transactional integrity
- Clean API design
- Independent component boundaries
- Idempotent order submission
- Asynchronous stock processing
- Recovery after temporary worker/service interruption
- A simple external integration surface for consuming accepted orders

The implementation should remain intentionally small. The goal is to demonstrate sound engineering decisions rather than build a complete ecommerce platform.

### Target runtime environment

The project must **run locally on Linux or macOS**. Implications:

- Everything ships via Docker Compose (app, worker, PostgreSQL) so the host OS
  doesn't matter — but all commands, scripts, and docs assume a POSIX shell
  (`bash`/`zsh`), not PowerShell or `cmd`.
- Use POSIX-style paths (`/`), LF line endings, and a `Makefile` (or plain shell
  scripts) for `make up` / `make demo` / `make test` style entry points.
- Add a `.gitattributes` enforcing `* text=auto eol=lf` so files authored on
  Windows check out with Unix line endings (protects `schema.sql`, shell scripts,
  and the `Dockerfile` entrypoint).
- No OS-specific dependencies. `psycopg[binary]` wheels cover Linux and macOS.

(Development may happen on Windows, but nothing Windows-specific may leak into
the committed project.)

---

## 2. Assignment Goals

### Task 1 — Orders Intake

Implement an Orders capability that:

- Accepts new orders through an API.
- Stores orders in PostgreSQL.
- Prevents duplicate orders when the same `order_ref` is submitted multiple times.
- Calculates the order total using product prices at the time of order.
- Exposes order creation and order lookup/status.
- Includes a seed/demo script that submits a burst of orders, including duplicates.

### Task 2 — Stock + Catch-up + Integration Surface

Implement stock handling that:

- Stores products and stock levels in PostgreSQL.
- Reduces stock when an accepted order is processed.
- Continues accepting orders while the stock worker is temporarily unavailable.
- Catches up pending stock updates after the worker resumes.
- Exposes current stock for a SKU.

Choose **Option A — Integration Surface**:

- Expose an append-only/polling feed of accepted orders.
- Provide a small consumer script.
- Demonstrate that consumers can catch up after being temporarily unavailable.

---

# 3. Recommended Architecture

Use a small modular system consisting of:

```text
                         Client
                           |
                           v
                    +--------------+
                    |   FastAPI    |
                    |  Orders API  |
                    +------+-------+
                           |
                           v
                    +--------------+
                    |  PostgreSQL  |
                    |              |
                    | Products     |
                    | Orders       |
                    | Order Items   |
                    | Outbox       |
                    +------+-------+
                           |
                     Polling Worker
                           |
                           v
                    +--------------+
                    | Stock Worker |
                    +------+-------+
                           |
                           v
                    Product Stock

External consumers:
                           |
                           v
                 GET /events/orders
```

### Architectural decision

Use:

- One FastAPI application/process for the API.
- One separate background worker process for stock processing.
- PostgreSQL as the source of truth.
- A PostgreSQL outbox table for durable events.
- A polling API as the external integration surface.

Do **not** introduce Kafka, RabbitMQ, Kubernetes, authentication, payments, or other infrastructure unless there is a clear reason. The assignment explicitly allows a minimal implementation.

---

# 4. Why the Outbox Pattern

Order creation and event creation should happen in the same PostgreSQL transaction.

```text
BEGIN

Create Order
Create Order Items
Create ORDER_ACCEPTED event

COMMIT
```

This prevents a failure such as:

```text
Order saved
but
event lost
```

The stock worker later polls the outbox:

```text
Outbox
   |
   +--> pending event
   |
   +--> Stock Worker
           |
           +--> update stock
           |
           +--> mark event processed
```

This means a temporary worker outage does not cause accepted orders to disappear.

---

# 5. Database Model

## 5.1 Products

Table: `products`

Suggested fields:

```text
id
sku              UNIQUE
name
price_cents
stock
created_at
updated_at
```

Example:

```json
{
  "sku": "BAN-001",
  "name": "Bananas 1kg",
  "price_cents": 199,
  "stock": 50
}
```

---

## 5.2 Orders

Table: `orders`

Suggested fields:

```text
id
order_ref        UNIQUE
customer_id
total_cents
status
created_at
```

The database must enforce:

```text
UNIQUE(order_ref)
```

Do not rely solely on an application-level "does this order exist?" check because concurrent requests can bypass that check.

---

## 5.3 Order Items

Table: `order_items`

Suggested fields:

```text
id
order_id
sku
quantity
unit_price_cents
line_total_cents
```

The `unit_price_cents` value is intentionally stored on the order item.

This creates a historical price snapshot.

Example:

```text
Product today:
BAN-001 = 199 cents

Order:
BAN-001 = 199 cents

Product tomorrow:
BAN-001 = 249 cents

Existing order:
still 199 cents
```

---

## 5.4 Outbox Events

Table: `outbox_events`

Suggested fields:

```text
id
event_type
aggregate_id
payload
created_at
processed_at
```

Example:

```json
{
  "event_type": "order.accepted",
  "order_ref": "web-100045",
  "items": [
    {
      "sku": "BAN-001",
      "qty": 2
    },
    {
      "sku": "APL-003",
      "qty": 1
    }
  ]
}
```

`processed_at = NULL` means the stock worker still needs to process the event.

---

# 6. API Design

Keep the API intentionally small.

## Create Order

```http
POST /orders
```

Request:

```json
{
  "order_ref": "web-100045",
  "customer_id": "cust-42",
  "items": [
    {
      "sku": "BAN-001",
      "qty": 2
    },
    {
      "sku": "APL-003",
      "qty": 1
    }
  ]
}
```

Response:

```json
{
  "order_ref": "web-100045",
  "status": "accepted",
  "total_cents": 697
}
```

---

## Get Order

```http
GET /orders/{order_ref}
```

Example response:

```json
{
  "order_ref": "web-100045",
  "customer_id": "cust-42",
  "status": "accepted",
  "total_cents": 697,
  "items": [
    {
      "sku": "BAN-001",
      "qty": 2,
      "unit_price_cents": 199,
      "line_total_cents": 398
    },
    {
      "sku": "APL-003",
      "qty": 1,
      "unit_price_cents": 299,
      "line_total_cents": 299
    }
  ]
}
```

---

## Get Stock

```http
GET /products/{sku}/stock
```

Example:

```json
{
  "sku": "BAN-001",
  "stock": 48
}
```

---

## Order Accepted Feed

Option A integration surface:

```http
GET /events/orders?after_id=100
```

Example:

```json
{
  "events": [
    {
      "id": 101,
      "type": "order.accepted",
      "order_ref": "web-100045",
      "occurred_at": "2026-09-07T08:15:00Z"
    },
    {
      "id": 102,
      "type": "order.accepted",
      "order_ref": "web-100046",
      "occurred_at": "2026-09-07T08:15:03Z"
    }
  ]
}
```

The feed should support replay/catch-up using `after_id`.

---

## Health

Optional:

```http
GET /health
```

Example:

```json
{
  "status": "ok"
}
```

---

# 7. Order Processing Flow

The order creation flow should be:

```text
POST /orders
     |
     v
Validate request
     |
     v
Load product prices
     |
     v
Calculate total
     |
     v
BEGIN TRANSACTION
     |
     +--> Create order
     |
     +--> Create order items
     |
     +--> Create ORDER_ACCEPTED outbox event
     |
     v
COMMIT
     |
     v
Return accepted
```

The API should **not** wait for the stock worker.

This deliberately creates eventual consistency between:

```text
Order accepted
```

and:

```text
Stock updated
```

---

# 8. Duplicate Order Handling

This is unhappy path #1.

### Same `order_ref`, different body

`order_ref` is the identity of the order. If a duplicate submission arrives with
a *different* body (different items or customer), the first stored order wins:
the API returns the original order unchanged and ignores the new body. This keeps
idempotency simple and predictable and avoids trying to reconcile conflicting
versions of "the same" order — which is out of scope for this exercise.

Submit:

```text
web-100045
```

multiple times.

Expected result:

```text
Request 1 -> order created
Request 2 -> existing order returned
Request 3 -> existing order returned
```

The database should contain only:

```text
1 order
1 set of order items
1 accepted event
```

Recommended behaviour:

```text
First submission:
201 Created

Duplicate submission:
200 OK
```

The response may include:

```json
{
  "order_ref": "web-100045",
  "status": "accepted",
  "total_cents": 697,
  "duplicate": true
}
```

The critical requirement is that a duplicate does not create another order or another stock update.

---

# 9. Stock Worker

The stock worker should:

1. Poll pending outbox events.
2. Process `order.accepted` events.
3. Reduce stock.
4. Mark the event processed.
5. Repeat.

Simplified flow:

```text
Worker
  |
  v
Find pending events
  |
  v
Process event
  |
  +--> Update product stock
  |
  +--> Mark event processed
  |
  v
Sleep briefly
  |
  v
Repeat
```

The stock update and event acknowledgement should occur in the same PostgreSQL transaction.

Conceptually:

```text
BEGIN

SELECT id, payload
FROM outbox_events
WHERE processed_at IS NULL
ORDER BY id
FOR UPDATE SKIP LOCKED        -- claim rows no other worker holds
LIMIT N

UPDATE products
SET stock = stock - quantity

UPDATE outbox_events
SET processed_at = now()

COMMIT
```

This prevents the worker from updating stock and then losing the processing state.

### Concurrency: `FOR UPDATE SKIP LOCKED`

The worker claims pending events with `SELECT ... FOR UPDATE SKIP LOCKED`. This
means the design is safe to run as more than one worker process: each row is
locked by exactly one worker for the duration of its transaction, and other
workers skip locked rows instead of blocking or double-processing. A single
worker is sufficient for the exercise, but this keeps the door open to
horizontal scaling with no code change and is a clean talking point.

### Delivery semantics: at-least-once

Because stock update and event acknowledgement commit together, an event is
either fully processed or not processed at all — a worker crash mid-event rolls
back and the event stays pending for the next poll. This gives **at-least-once**
processing. For this exercise the stock reduction is applied once per event via
the `processed_at` guard, so no separate dedupe is needed on the worker side.

### Stock going negative

Overselling prevention is explicitly out of scope. The worker applies
`stock = stock - qty` unconditionally and **allows stock to go negative**. This
is a deliberate choice: it keeps the happy path trivial and makes the point that
inventory reservation / oversell handling is a separate concern we've scoped out.
The README notes this and lists "reject or clamp at zero" as a future improvement.

---

# 10. Stock Outage Demonstration

This is unhappy path #2.

Initial state:

```text
BAN-001 stock = 50
```

Pause/stop the stock worker.

Submit orders:

```text
Order A -> BAN-001 x 2
Order B -> BAN-001 x 3
Order C -> BAN-001 x 1
```

Orders API should continue accepting them.

Expected state while worker is stopped:

```text
Orders:
A = accepted
B = accepted
C = accepted

Stock:
50

Outbox:
A = pending
B = pending
C = pending
```

Resume the worker.

Expected result:

```text
A -> -2
B -> -3
C -> -1

Total reduction = 6

Final stock:
44
```

Outbox events should then show:

```text
A = processed
B = processed
C = processed
```

This demonstrates durable catch-up after a temporary interruption.

---

# 11. External Integration Consumer

Create:

```text
scripts/consume_order_events.py
```

The consumer should:

1. Store/read a `last_event_id`.
2. Poll `/events/orders?after_id=...`.
3. Print new accepted orders.
4. Advance its last processed event ID.

Example:

```text
Received event 101: order.accepted web-100045
Received event 102: order.accepted web-100046

Waiting...

Received event 103: order.accepted web-100047
```

If the consumer is stopped temporarily:

```text
last_event_id = 102
```

When it restarts:

```text
GET /events/orders?after_id=102
```

It should receive event 103 and anything after it.

This demonstrates that the integration surface is replayable and does not depend on a consumer being online continuously.

---

# 12. Demo / Seed Script

Create:

```text
scripts/demo.py
```

The script should:

### Seed products

```text
BAN-001   Bananas 1kg   199   50
APL-003   Apples 1kg    299   30
MLK-001   Milk 1L       189   20
```

### Submit a burst

Example:

```text
web-100001
web-100002
web-100003
web-100002   duplicate
web-100004
web-100001   duplicate
```

Print results:

```text
Submitting web-100001 -> CREATED
Submitting web-100002 -> CREATED
Submitting web-100003 -> CREATED
Submitting web-100002 -> DUPLICATE
Submitting web-100004 -> CREATED
Submitting web-100001 -> DUPLICATE

Requests submitted: 6
Unique orders:       4
```

---

# 13. Project Structure

Recommended structure:

```text
order-stock-system/
|
├── app/
│   ├── main.py                 # FastAPI app factory + startup (apply schema.sql)
│   |
│   ├── api/
│   │   ├── orders.py           # POST /orders, GET /orders/{order_ref}
│   │   ├── products.py         # GET /products/{sku}/stock
│   │   └── events.py           # GET /events/orders?after_id=
│   |
│   ├── services/
│   │   ├── order_service.py    # order intake + total calc + outbox write (1 txn)
│   │   └── stock_service.py    # stock read/reduce logic
│   |
│   ├── repositories/           # thin psycopg3 data-access functions
│   │   ├── order_repository.py
│   │   ├── product_repository.py
│   │   └── outbox_repository.py
│   |
│   ├── schemas/                # Pydantic request/response models
│   │   ├── orders.py
│   │   └── products.py
│   |
│   └── db/
│       ├── pool.py             # psycopg3 connection pool + txn helper
│       └── schema.sql          # single idempotent DDL file (source of truth)
│
├── worker/
│   └── stock_worker.py         # polling loop, FOR UPDATE SKIP LOCKED, pausable
│
├── scripts/
│   ├── seed_products.py
│   ├── demo.py
│   └── consume_order_events.py
│
├── tests/
│   ├── test_orders.py
│   ├── test_duplicates.py
│   └── test_stock_catchup.py
│
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

Note: no `models/` (ORM) or `migrations/` directory — `db/schema.sql` is the
single source of truth for the schema, and `repositories/` hold plain
parameterised SQL.

---

# 14. Technology Stack

Core:

```text
Python 3.12+
FastAPI
PostgreSQL
psycopg 3          (raw SQL — no ORM)
Pydantic           (request/response validation)
pytest
httpx              (test client + consumer script)
```

### Why raw psycopg3 instead of SQLAlchemy + Alembic

This is a deliberate, scoped decision:

- The exercise is about **transactional correctness** (outbox, atomic stock +
  ack). Raw SQL keeps those transaction boundaries visible in the code rather
  than hidden behind an ORM session/unit-of-work.
- The schema is four fixed tables with no evolving migration history, so a
  single `schema.sql` applied at startup beats an Alembic setup for "create the
  schema from scratch."
- Fewer moving parts to read and explain.

SQLAlchemy + Alembic would be the right call once the domain grows past a
handful of tables or the schema starts changing under real traffic. Noting that
trade-off is itself a talking point (see §18).

Schema management: a single idempotent `schema.sql` (with `CREATE TABLE IF NOT
EXISTS` / constraints) applied on startup or via a tiny `init_db` script.

Development/infrastructure:

```text
Docker
Docker Compose
pgAdmin (optional)
```

Do not add additional infrastructure unless it provides clear value.

---

# 15. Development Phases

## Phase 1 — Repository and Environment

Tasks:

- Create Git repository.
- Create Python virtual environment.
- Create FastAPI project.
- Add PostgreSQL.
- Add Docker Compose (app, worker, db).
- Add `.gitattributes` (`* text=auto eol=lf`) and a `Makefile` with POSIX entry
  points (`make up`, `make demo`, `make test`, `make down`).
- Configure environment variables (`.env.example`).
- Create initial README.

Deliverable:

```text
FastAPI -> PostgreSQL
```

running on Linux/macOS via `docker compose up` (or `make up`).

---

## Phase 2 — Database and Migrations

Tasks:

- Create `products`.
- Create `orders`.
- Create `order_items`.
- Create `outbox_events`.
- Add foreign keys.
- Add unique constraint on `orders.order_ref`.
- Add index on `outbox_events(processed_at)` (partial, `WHERE processed_at IS NULL`)
  so the worker's pending-events poll stays fast.
- Add index on `outbox_events(id)` for the `after_id` feed cursor (PK already covers this).
- Write it all into a single idempotent `db/schema.sql`.
- Apply schema on app startup (and via a small `init_db` script for scripts/tests).

Deliverable:

```text
Database schema can be created from scratch by applying schema.sql.
```

---

## Phase 3 — Product Seeding

Tasks:

- Implement seed script.
- Add sample products.
- Verify products using pgAdmin or SQL.

Deliverable:

```text
BAN-001
APL-003
MLK-001
```

available in PostgreSQL.

---

## Phase 4 — Orders API

Tasks:

- Implement `POST /orders`.
- Implement `GET /orders/{order_ref}`.
- Validate request models.
- Calculate order total.
- Snapshot item prices.
- Persist order and items.
- Create outbox event in same transaction.

Deliverable:

```text
Create order -> PostgreSQL -> accepted response
```

---

## Phase 5 — Idempotency

Tasks:

- Add/verify unique `order_ref`.
- Handle duplicate submissions.
- Return existing order on duplicate.
- Ensure only one outbox event exists.

Deliverable:

```text
Same order_ref submitted 3 times
        |
        v
One order
One event
```

---

## Phase 6 — Stock Worker

Tasks:

- Implement polling worker.
- Read pending outbox events with `FOR UPDATE SKIP LOCKED` (multi-worker safe).
- Update stock (allowed to go negative — oversell handling is out of scope).
- Mark event processed in the same transaction as the stock update.
- Use PostgreSQL transactions.
- Make worker restart-safe (pending events survive; at-least-once processing).

Deliverable:

```text
ORDER_ACCEPTED
      |
      v
Stock Worker
      |
      v
Stock reduced
```

---

## Phase 7 — Outage / Catch-up

Tasks:

- Add a simple worker pause/stop mechanism.
- Stop worker.
- Submit orders.
- Verify orders are accepted.
- Verify outbox events remain pending.
- Restart worker.
- Verify stock catches up.

Deliverable:

A repeatable demonstration of unhappy path #2.

---

## Phase 8 — Integration Surface

Tasks:

- Implement `GET /events/orders?after_id=`.
- Return ordered accepted-order events.
- Implement consumer script.
- Persist consumer offset.
- Demonstrate consumer catch-up.

Deliverable:

```text
Order Accepted
      |
      v
Event Feed
      |
      v
External Consumer
```

---

## Phase 9 — Automated Tests

Minimum tests:

```text
test_create_order
test_order_total_uses_snapshot_price
test_duplicate_order_ref
test_stock_update
test_worker_catch_up_after_outage
```

Focus particularly on the two required unhappy paths.

---

## Phase 10 — Documentation and Demo

README should include:

1. Architecture diagram.
2. Setup instructions.
3. Database design.
4. API examples.
5. Idempotency explanation.
6. Outbox explanation.
7. Stock worker explanation.
8. Outage simulation.
9. Integration feed explanation.
10. Test instructions.
11. Known limitations and out-of-scope functionality.

---

# 16. Definition of Done

The project is complete when all of the following work:

### Orders

- [ ] Product data persists in PostgreSQL.
- [ ] Orders persist in PostgreSQL.
- [ ] Order items persist in PostgreSQL.
- [ ] Prices are captured at order time.
- [ ] Order totals are calculated correctly.
- [ ] `order_ref` is unique.
- [ ] Duplicate submissions do not create duplicate orders.
- [ ] Order details/status can be retrieved.

### Stock

- [ ] Stock persists in PostgreSQL.
- [ ] Stock updates are performed by a worker.
- [ ] Stock can be queried by SKU.
- [ ] Worker processing is transactional.
- [ ] Pending events survive worker interruption.
- [ ] Worker catches up after restart.

### Integration

- [ ] Accepted-order events are available externally.
- [ ] Events are ordered.
- [ ] Consumer can poll from an event ID.
- [ ] Consumer can catch up after being offline.

### Demonstrations

- [ ] Duplicate submission scenario demonstrated.
- [ ] Worker outage/catch-up scenario demonstrated.

### Quality

- [ ] Automated tests pass.
- [ ] README explains architecture and trade-offs.
- [ ] Application can be started from a clean environment.
- [ ] No critical state depends solely on in-memory data.

---

# 17. Out of Scope

Keep the implementation focused.

Do not implement:

- Authentication/authorization
- Payment processing
- Refunds
- Discounts/promotions
- Shopping carts
- Customer management
- Multiple warehouses
- Shipping/courier integrations
- Returns
- Advanced inventory reservation
- Overselling prevention beyond the stated requirements
- Kafka/RabbitMQ unless needed
- Kubernetes
- Distributed tracing infrastructure
- Complex monitoring
- Full production security hardening

These can be mentioned as future improvements.

---

# 18. Senior Engineering Talking Points

The implementation should remain minimal, but the design should demonstrate awareness of real production concerns.

Be prepared to explain:

### Idempotency

Why `order_ref` has a database uniqueness constraint and why application-level duplicate checks alone are insufficient.

### Transaction boundaries

Why order, order items, and the outbox event are committed atomically.

### Eventual consistency

Why an accepted order can temporarily exist before stock reflects it.

### Outbox pattern

Why events are persisted before being processed and why this prevents lost events.

### Worker restartability

Why pending events remain in PostgreSQL and can be processed after a worker restart.

### Replayability

Why the external event feed uses an event ID/cursor rather than relying on a live connection.

### Independent components

Why the API and stock worker can run independently and be scaled or replaced separately.

### Worker concurrency

Why `FOR UPDATE SKIP LOCKED` lets the worker scale to multiple processes with no
code change, and why processing is at-least-once made effectively once via the
`processed_at` guard.

### Tooling restraint

Why raw psycopg3 + a single `schema.sql` was chosen over SQLAlchemy + Alembic
for a four-table, fixed schema — and when the ORM/migration trade-off flips.

### Minimalism

Why the design deliberately avoids unnecessary infrastructure for this exercise.

---

# 19. Future Evolution

If this system had to grow substantially, possible next steps would be:

```text
Current:

FastAPI
   |
PostgreSQL
   |
Stock Worker


Future:

                    API Gateway
                         |
             +-----------+-----------+
             |                       |
       Order Service          Product Service
             |
         Event Bus
             |
     +-------+--------+----------+
     |                |          |
 Inventory         Payment    Fulfilment
 Service           Service     Service
     |
 Inventory DB
```

Potential future infrastructure:

- RabbitMQ / Kafka / cloud messaging
- Redis for caching
- Separate databases per service
- Container orchestration
- Horizontal scaling
- Distributed tracing
- Metrics/observability
- More sophisticated inventory reservation
- Dead-letter queues
- Retry policies
- Event versioning

These should only be introduced when scale or operational requirements justify them.

---

# 20. Final Architecture Principle

The central design principle for this project is:

> Keep the synchronous order intake path small and reliable, persist the important state in PostgreSQL, and move recoverable downstream work behind durable events.

In particular:

```text
Client
  |
  v
FastAPI
  |
  +--> PostgreSQL transaction
          |
          +--> Order
          +--> Order Items
          +--> ORDER_ACCEPTED event
                    |
                    v
               Stock Worker
                    |
                    v
                Stock update
```

This provides a simple implementation while demonstrating the architectural thinking expected from a senior engineer.
