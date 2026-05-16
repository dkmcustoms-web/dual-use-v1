"""Initialize the sandbox database by running schema.sql.

Usage:
    python -m db.init_db

Safe to run multiple times — all DDL uses IF NOT EXISTS.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from db.connection import get_engine


def main() -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")

    engine = get_engine()
    # Postgres can run multiple statements when sent as one block via
    # exec_driver_sql; SQLAlchemy's text() needs them separated.
    with engine.begin() as conn:
        # Use the raw DBAPI cursor so we can send the whole file at once.
        raw_conn = conn.connection
        cur = raw_conn.cursor()
        cur.execute(sql)
        cur.close()
    print("✓ Schema initialized successfully.")


if __name__ == "__main__":
    main()
