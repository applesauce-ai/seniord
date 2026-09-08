## The whole thing in a paragraph

There are two processes that don't talk to each other directly: a FastAPI app and
a background worker. They only share a PostgreSQL database, and Postgres is the
source of truth for everything. When an order comes in, the API checks it,
reserves the stock, and writes an "order accepted" event into an outbox table,
all in the same transaction. The worker comes along afterwards, reads that outbox,
and applies the actual stock reduction. Anything else that wants to know about
accepted orders either polls a feed (`GET /events/orders`) or gets them pushed to
a webhook (I wired it to Discord). The API never waits on the worker, so it stays
fast and stays up even if the worker is down. Nothing important is kept in memory.

```
client → POST /orders ─┐
                       │  (one transaction)
                       ▼
                 PostgreSQL: orders + order_items + outbox_events   ← source of truth
                       │
             ┌─────────┴──────────┐
             ▼                    ▼
      Stock worker          Feed / webhook consumers
   (settles stock)        (GET /events/orders, Discord)
```

---

## How the app is layered

`app/` is split into layers so each one has a single job. A request comes in at
the top and works its way down to the database:

```
HTTP request
   │
   ▼
app/api/*          routes, status codes, request/response models
   │
   ▼
app/services/*     the actual logic: transactions, rules, orchestration
   │
   ▼
app/repositories/* data access: one parameterised SQL query per function
   │
   ▼
app/db/*           connection pool, transaction helper, schema
   │
   ▼
PostgreSQL
```

The api layer only deals with HTTP and never touches SQL. The services are where
the real work happens: they open the transaction, enforce the rules, and call
whatever repositories they need. Repositories stay dumb on purpose, just one SQL
statement each, and they never commit. Whoever opened the transaction is the one
that commits it. Pydantic schemas sit off to the side and handle validating what
comes in and shaping what goes out.

The payoff is that the fiddly stuff (idempotency, reservations, the outbox) reads
cleanly, because the rule lives in the service and the SQL lives in the
repository, and neither is tangled up in the other.

---

## A note on the SQL

All the SQL sits inline in the repository layer, parameterised, one query per
function. That was a deliberate call for a project this size, not a corner I cut.
The database still does the important enforcement itself: the unique `order_ref`,
the `stock >= reserved` check, the foreign keys. And the part that actually needs
to be careful about concurrency already lives in the database, in the atomic
reservation update and the `SKIP LOCKED` poll. That's the database doing the hard
work, and a stored procedure wouldn't make any of it safer.

If this grew, the first thing I'd add is a real migration tool (Alembic or
Flyway) instead of applying `schema.sql` on startup, and probably an ORM once
there were enough queries that maintaining them by hand got tedious. I wouldn't
default to stored procedures. They have their uses, like enforcing a rule no
matter which service touches a table, or cutting down a chatty multi-step
operation, but they're harder to version, review and test because the logic ends
up split between the app and the database. For a system this size, keeping it in
the app is easier to follow and easier to reason about.

---

## A note on the primary keys

The primary keys are all plain integers (`SERIAL` / `BIGSERIAL`). For this project
that's the right default. They're small, they index and join well, and because
they only ever count upward the inserts stay cheap and don't fragment the index.

The usual reason people reach for GUIDs is security: sequential ints are
guessable, so if you put them in a URL someone can walk `/orders/1`, `/orders/2`
and either count your data or poke at rows they shouldn't. The thing is, I never
expose those integer ids. An order's public handle is its `order_ref` (like
`web-100045`) and a product's is its `sku`, so there's nothing sequential to
enumerate. The one place an int id is public is the event feed's `after_id`, and
that's deliberate: it's a position in an append-only log, not a resource handle,
so walking it just replays accepted-order events, which is the whole point of the
feed.

If I did need non-guessable public ids, or ids that could be generated in more
than one place without coordinating, I'd move to UUIDs, but I'd be careful about
which kind. A random v4 UUID is 16 bytes instead of 4, so it bloats every index
and every foreign key pointing at it, and because it's random it scatters inserts
across the B-tree and fragments it, which really starts to hurt once the tables
are large. So I'd use a time-ordered UUID (v7, or a ULID): still non-sequential to
the outside world, but it inserts in order like an int does. The other option is
to keep the int key for internal joins and add a separate public UUID column only
on the things that leave the system, so you get the security without paying the
cost on every join. For this project the `order_ref` / `sku` approach already
gives me that separation for free.

---

## The files in the root

`docker-compose.yml` is the thing that runs everything. It defines the `db`
(Postgres 16), the `api`, and the `worker`, plus two extras that only start when
you ask for them (the webhook forwarder and the standalone consumer).

`Dockerfile` builds one image for all the Python. The api, worker, forwarder and
consumer are all the same image with a different command.

`Makefile` is just shortcuts so I'm not typing long `docker compose` lines: `up`,
`down`, `seed`, `demo`, `outage-demo`, `test`, `consumer-up`, `webhook` and so on.

The rest:
- `requirements.txt` — FastAPI, uvicorn, psycopg 3, pydantic and pydantic-settings, pytest, httpx.
- `pytest.ini` — points pytest at `tests/` and puts the repo root on the path.
- `.env` / `.env.example` — config (DB URL, worker interval, webhook URL). `.env` is gitignored because it holds the real Discord webhook.
- `.gitattributes` — forces LF line endings so nothing breaks on the Linux/macOS target even though I'm on Windows.
- `README.md` — the main write-up: setup, API reference, design decisions, the demos.
- `Senior_D_Project_Plan.md` — the plan I wrote up front, kept around to show the thinking.
- `postman_collection.json` — every endpoint, ready to import into Postman.

---

## `app/` — the FastAPI application

**`main.py`** is the entry point. It builds the app, plugs in all the routers, and
has a startup hook that opens the connection pool and applies the schema before
any request gets served, so the database is always ready to go.

**`config.py`** reads configuration from the environment with pydantic-settings
(`DATABASE_URL`, worker poll interval, and so on) and hands back one typed
`settings` object that the rest of the code uses.

**`db/`** is the database plumbing:
- `schema.sql` is the one place the schema is defined. It's idempotent, so it's
  safe to run on every startup, which is exactly what happens. No ORM and no
  migration tool, which is the right amount of machinery for four fixed tables.
- `pool.py` has the psycopg3 pool, `apply_schema()`, and the `transaction()`
  context manager that commits on success and rolls back on an error. That
  `transaction()` helper is genuinely the centre of the whole design, because
  it's the atomic unit the outbox pattern leans on.

**`schemas/`** is the API's public shape, kept separate from how anything is
stored:
- `orders.py` — the create-order request (with the validation: non-empty SKUs,
  `qty > 0`), the create response, and the full order detail.
- `products.py` — the stock response, the product list item (which only exposes
  whether something is in stock, not how many), the create-product request, and
  the stock-update request that insists on exactly one of `set` or `add`.
- `events.py` — the shapes for the feed.

**`repositories/`** is the data access. Every function takes a connection and
runs one query, and none of them commit:
- `order_repository.py` — the idempotent order insert (`ON CONFLICT (order_ref)
  DO NOTHING`), inserting line items, and reading an order back with its items.
- `product_repository.py` — prices, the stock row, `reserve` (the atomic guard
  that stops overselling), `settle`, setting stock, creating products, and listing.
- `outbox_repository.py` — writing an event, claiming pending ones with `FOR
  UPDATE SKIP LOCKED`, marking them processed, and the query behind the feed.
- `webhook_repository.py` — the delivery table for the webhook: enqueue, claim
  what's due, and mark things delivered, retrying or dead.

**`services/`** is where the rules live and the transactions get opened:
- `order_service.py` is the important one. Validating the SKUs, catching a
  duplicate before doing anything, reserving stock atomically, then writing the
  order, its items and the outbox event, all inside a single transaction. If the
  stock isn't there it raises and the whole thing rolls back, so a rejected order
  leaves nothing behind.
- `stock_service.py` is the worker's side: claim pending events, settle the stock,
  mark them done, in one transaction.
- `product_service.py` covers the catalogue: listing (availability only), reading
  stock, adding or setting stock behind a row lock, and creating a product.
- `event_service.py` reads the accepted-order feed for outside consumers.

**`api/`** is the HTTP layer, one file per area:
- `orders.py` — `POST /orders` and `GET /orders/{order_ref}`, turning the domain
  errors into the right status codes (400, 404, 409).
- `products.py` — listing, creating, reading stock, and the PATCH to add/set stock.
- `events.py` — the `GET /events/orders?after_id=` feed.
- `console.py` — serves the live console page, its stats endpoint, and the demo
  helpers (`/console/reset`, `/console/set_stock`).

**`static/console.html`** is the dark dashboard the API serves. Because it's on
the same origin it can make real calls to the API. It's got the live monitor and
sparkline, a traffic generator, a tabbed playground for hitting every endpoint,
and the seed/burst and oversell demos. It's only a demo aid, nothing depends on it.

---

## `worker/` — the stock worker

`stock_worker.py` is a small loop in its own container: check the outbox, settle
whatever's pending, sleep, repeat. It shuts down cleanly on a signal and keeps
going if a cycle throws. The whole reason it's a separate process is so it can be
stopped, restarted or scaled without touching the API, and stopping it is exactly
how I demo the outage and catch-up. It runs the same `process_available()` the
tests exercise, so there's no separate "worker logic" to trust.

---

## `scripts/` — the runnable helpers

- `seed_products.py` — drops in the sample catalogue (bananas, apples, milk). Safe
  to run repeatedly.
- `demo.py` — the Task 1 demo: reset, seed, fire a burst of orders with some
  duplicates mixed in, and print what was created versus what was a duplicate.
- `outage_demo.sh` — the outage story end to end: stop the worker, submit orders,
  show them accepted with the events piling up, restart the worker, watch stock
  catch up.
- `consume_order_events.py` — the external consumer. Polls the feed, prints each
  order to stdout, and remembers its position in a file. It only needs `httpx` and
  a URL, no project code, so it really is standing in for "some other system". Set
  `CONSUMER_CUSTOMER_ID` and it becomes a filtered subscription that only receives
  one customer's orders (the standalone container is scoped to `cust-42` this way).
- `webhook_forwarder.py` — the push side. A durable little delivery worker that
  reads the outbox and POSTs each accepted order to a webhook, with backoff and a
  dead-letter after too many failures.

---

## `tests/` — integration tests against a real Postgres

These run against their own `orders_test` database so they never step on the dev
data or fight the running worker. `conftest.py` sets up the test client, wipes the
tables between tests, and applies the schema once at the start.

- `test_orders.py` — creating an order, the total coming from the catalogue, the
  price snapshot surviving a later price change, unknown SKUs, 404s and validation.
- `test_duplicates.py` — the duplicate story: the same `order_ref` three times
  still leaves one order and one event.
- `test_stock_catchup.py` — reserve now, settle later, and the backlog draining
  after the worker was "down".
- `test_oversell.py` — not enough stock gives a 409, a multi-item order is all or
  nothing, and two orders racing for the last units only lets one win.
- `test_products.py` — the list showing availability not a count, add/set stock,
  refusing to drop stock below what's reserved, creating and rejecting duplicate
  products, and the zero-stock "unavailable" message.

---

## `.github/workflows/ci.yml`

The CI pipeline. On a push or PR to `main` it spins up a Postgres container,
installs the deps, runs the tests, publishes the results (a summary on the run
plus the raw file as an artifact), and posts the outcome to Discord. It's the
"tests run on every commit" part.

---

## Following one request all the way through

**A new order (`POST /orders`):**
1. `api/orders.py` validates the body against `schemas/orders.py`. Bad input never
   makes it past here (422).
2. `order_service.py` opens one transaction and:
   - loads the prices; an unknown SKU bails out with a 400;
   - tries the idempotent insert. If the `order_ref` already exists it just hands
     back the existing order (200, `duplicate: true`) and touches no stock;
   - reserves each line. If any line can't be reserved it's a 409 and the whole
     transaction rolls back;
   - writes the items and an `order.accepted` event;
   - commits, so from here on the order and its event exist together or not at all.
3. The response goes back straight away (201). Stock is reserved at this point but
   not physically settled yet.

**Settling the stock (the worker, later):**
1. `stock_worker.py` calls `process_available()`.
2. It claims the pending events with `FOR UPDATE SKIP LOCKED`, settles the stock
   (on-hand and reserved both come down), and marks the event processed, all in one
   transaction.

**Other systems reading accepted orders:**
- Pull: they hit `GET /events/orders?after_id=N` and page forward, optionally with
  `&customer_id=…` to only see one customer's orders. That's what the consumer
  script does (it's scoped to a single customer).
- Push: the webhook forwarder reads the outbox and POSTs to Discord, tracking each
  delivery so it can retry and eventually dead-letter.

---

## The five things I want to be able to defend

1. Idempotency is a database unique constraint, not an app-level check, so it holds
   up even when two identical requests land at once.
2. The transactional outbox means an order and its event commit together. An
   accepted order can't exist without its event, so nothing gets lost.
3. Reserving at checkout and settling later stops overselling immediately while
   still keeping the worker asynchronous and its recovery story intact.
4. `FOR UPDATE SKIP LOCKED` is what makes the worker (and the webhook forwarder)
   safe to run more than once and at-least-once by nature.
5. The feed is replayable with a cursor, so consumers own their position and can
   drop offline and catch up. The webhook is just another reader of that same log.
