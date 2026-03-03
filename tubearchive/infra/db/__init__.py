"""DB 인프라 공용 엔트리포인트."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from tubearchive.infra.db.schema import init_database


@contextmanager
def database_session() -> Generator[sqlite3.Connection]:
    """DB 연결을 자동으로 닫아주는 context manager."""
    conn = init_database()
    try:
        yield conn
    finally:
        conn.close()


__all__ = ["database_session"]
