"""Postgres connection via SQLAlchemy.

Reads DATABASE_URL from environment (Neon connection string).
Used by every page and service that needs DB access.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()  # load .env if present (no-op on Railway where env is provided)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a singleton SQLAlchemy engine.

    Neon's free tier uses pgbouncer connection pooling, so we keep our own
    pool small. `pool_pre_ping` survives idle disconnects.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in, "
            "or set it as a Railway environment variable."
        )
    # SQLAlchemy expects 'postgresql://', not 'postgres://'
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return create_engine(
        url,
        pool_size=2,
        max_overflow=3,
        pool_pre_ping=True,
        pool_recycle=300,
    )


def run_query(sql: str, params: dict | None = None) -> list[dict]:
    """Execute a SELECT and return list-of-dicts."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


def execute(sql: str, params: dict | None = None) -> None:
    """Execute an INSERT/UPDATE/DELETE/DDL statement."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def execute_many(sql: str, rows: list[dict]) -> None:
    """Batch insert/update."""
    if not rows:
        return
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(sql), rows)
