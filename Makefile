# POSIX entry points. Targets assume Docker Compose v2 (`docker compose`).

.PHONY: up down logs demo seed consume test fmt

up:            ## Build and start db + api + worker
	docker compose up --build -d
	@echo "API:  http://localhost:8000"
	@echo "Docs: http://localhost:8000/docs"

down:          ## Stop everything and remove the db volume
	docker compose down -v

logs:          ## Tail logs for all services
	docker compose logs -f

seed:          ## Seed sample products
	docker compose exec api python -m scripts.seed_products

demo:          ## Seed + submit a burst of orders (with duplicates)
	docker compose exec api python -m scripts.demo

consume:       ## Run the consumer inline (foreground; dies if the API container stops)
	docker compose exec api python -m scripts.consume_order_events

consumer-up:   ## Run the consumer as its own container (survives stopping the API)
	docker compose --profile consumer up -d --build consumer
	docker compose logs -f consumer

consumer-stop: ## Stop the standalone consumer container
	docker compose --profile consumer stop consumer

webhook:       ## Start the Discord/webhook forwarder (needs WEBHOOK_URL in .env)
	docker compose --profile webhook up -d --build webhook-forwarder
	docker compose logs -f webhook-forwarder

webhook-stop:  ## Stop the webhook forwarder
	docker compose --profile webhook stop webhook-forwarder

outage-demo:   ## Demonstrate worker outage + catch-up (unhappy path #2)
	bash scripts/outage_demo.sh

test:          ## Run the test suite (isolated 'orders_test' database)
	-docker compose exec -T db psql -U orders -d orders -c "CREATE DATABASE orders_test" 2>/dev/null
	docker compose exec -T -e DATABASE_URL=postgresql://orders:orders@db:5432/orders_test api pytest
