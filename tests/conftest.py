from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.pool import apply_schema, transaction
from app.main import app
from app.repositories import product_repository as products


@pytest.fixture(scope="session", autouse=True)
def _schema():
    # Ensure tables exist before the first truncation (fresh test database).
    apply_schema()
    yield


@pytest.fixture()
def client():
    # `with` runs the app lifespan, which ensures the schema exists.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_db():
    """Truncate all tables before every test so tests are independent."""
    with transaction() as conn:
        conn.execute(
            "TRUNCATE order_items, orders, outbox_events, products RESTART IDENTITY CASCADE"
        )
    yield


def seed_products() -> None:
    """Seed the catalogue used across tests."""
    with transaction() as conn:
        products.upsert_product(conn, "BAN-001", "Bananas 1kg", 199, 50)
        products.upsert_product(conn, "APL-003", "Apples 1kg", 299, 30)


def count(sql: str, params: tuple = ()) -> int:
    with transaction() as conn:
        return conn.execute(sql, params).fetchone()["count"]
