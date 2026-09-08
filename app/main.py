from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import console, events, orders, products
from app.db.pool import apply_schema, close_pool, get_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring up the pool and ensure the schema exists before serving traffic.
    get_pool()
    apply_schema()
    yield
    close_pool()


app = FastAPI(title="Order & Stock System", lifespan=lifespan)

app.include_router(orders.router)
app.include_router(products.router)
app.include_router(events.router)
app.include_router(console.router)


@app.get("/health")
def health() -> dict:
    with get_pool().connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
