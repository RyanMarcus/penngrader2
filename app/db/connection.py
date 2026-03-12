from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings


@contextmanager
def get_conn(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    settings = get_settings()
    with psycopg.connect(settings.pg_dsn, autocommit=autocommit, row_factory=dict_row) as conn:
        yield conn
