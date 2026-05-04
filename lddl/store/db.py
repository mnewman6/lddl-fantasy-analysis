"""DuckDB connection helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_schema(db_path: Path) -> None:
    """Create tables if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(SCHEMA_PATH.read_text())
    finally:
        conn.close()


@contextmanager
def connect(db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    try:
        yield conn
    finally:
        conn.close()
