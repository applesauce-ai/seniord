from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Module-level singleton. Each process (api, worker, a script) builds its own.
_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def transaction() -> Iterator[Connection]:
    """A connection wrapped in a single transaction.

    Commits on clean exit, rolls back on any exception. This is the unit that
    makes the outbox atomic: order + items + event either all commit or none do.
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def apply_schema() -> None:
    """Apply the idempotent schema. Safe to call on every startup."""
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    with get_pool().connection() as conn:
        conn.execute(ddl)
