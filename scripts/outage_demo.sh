#!/usr/bin/env bash
#
# Unhappy path #2 — stock worker outage with catch-up afterwards.
#
# Shows that while the stock worker is down: (a) the Orders API keeps accepting
# orders, (b) stock is unchanged, (c) events accumulate as pending in the outbox.
# After the worker resumes, the backlog drains and stock catches up.
#
# Run from the project root, with the stack already up (`make up`):
#     bash scripts/outage_demo.sh
set -euo pipefail

API="${API_BASE_URL:-http://localhost:8000}"
DC="docker compose"
PSQL="$DC exec -T db psql -U orders -d orders -tAc"

submit() {  # submit <order_ref> <sku> <qty>
  curl -s -o /dev/null -w "  submit %{http_code}  $1 ($2 x$3)\n" \
    -X POST "$API/orders" -H 'Content-Type: application/json' \
    -d "{\"order_ref\":\"$1\",\"customer_id\":\"cust-outage\",\"items\":[{\"sku\":\"$2\",\"qty\":$3}]}"
}

stock()   { curl -s "$API/products/$1/stock"; echo; }
pending() { $PSQL "SELECT count(*) FROM outbox_events WHERE processed_at IS NULL;"; }

echo "=== reset + seed ==="
$DC exec -T db psql -U orders -d orders -c \
  "TRUNCATE order_items, orders, outbox_events, products CASCADE;" >/dev/null
$DC exec -T api python -m scripts.seed_products
echo

echo "=== stop the stock worker (simulated outage) ==="
$DC stop worker
echo

echo "=== submit 3 orders for BAN-001 while the worker is DOWN ==="
submit web-outage-A BAN-001 2
submit web-outage-B BAN-001 3
submit web-outage-C BAN-001 1
echo

echo "=== state DURING outage ==="
echo -n "  stock:   "; stock BAN-001
echo    "  pending: $(pending) event(s)  (accepted + reserved; on-hand unchanged, available reduced)"
echo

echo "=== resume the stock worker ==="
$DC start worker
echo "  waiting for catch-up..."
sleep 4
echo

echo "=== state AFTER catch-up (expect on-hand 44, reserved 0, available 44, pending 0) ==="
echo -n "  stock:   "; stock BAN-001
echo    "  pending: $(pending) event(s)"
echo
echo "Done."
