"""Translate the legacy model scripts' SQL calls to psycopg connections.

The model sources are kept close to their original form while all runtime
connections target PostgreSQL. Only identifiers used by the six stock dataset
tables are translated; arbitrary SQL is not accepted.
"""

from __future__ import annotations

import re
from typing import Any

import psycopg


_TABLE_NAMES = {
    "stock_data_jp", "stock_data_kr", "stock_data_week_jp", "stock_data_week_kr",
    "stock_list_jp", "stock_list_kr",
}
_COLUMNS = {
    "open", "high", "low", "close", "volume", "transamnt", "5mvavg", "20mvavg",
    "50mvavg", "60mvavg", "120mvavg", "240mvavg", "upperband60_1",
    "lowerband60_1", "lowerband60_3", "di_plus", "di_minus", "adx",
}


def _postgres_identifier(identifier: str) -> str:
    normalized = identifier.lower()
    return f'"{normalized}"' if normalized[0].isdigit() else normalized


def _translate_query(query: str) -> str:
    def replace_identifier(match: re.Match[str]) -> str:
        value = match.group(0)
        normalized = value.lower()
        if normalized in _TABLE_NAMES or normalized in _COLUMNS:
            return _postgres_identifier(value)
        return value

    return re.sub(r"(?<![\w\"])(?:STOCK_[A-Z_]+|[0-9]+MvAvg|Open|High|Low|Close|Volume|TransAmnt|UpperBand60_1|LowerBand60_[13]|DI_(?:plus|minus)|ADX)(?![\w\"])", replace_identifier, query, flags=re.IGNORECASE)


class Cursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    def execute(self, query: str, params: Any = None) -> None:
        self._cursor.execute(_translate_query(query), params)

    def executemany(self, query: str, params_seq: Any) -> None:
        self._cursor.executemany(_translate_query(query), params_seq)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._cursor.fetchall()

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._cursor.fetchone()

    def __enter__(self) -> "Cursor":
        self._cursor.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self._cursor.__exit__(*args)

    def close(self) -> None:
        self._cursor.close()


class Connection:
    def __init__(self, connection: psycopg.Connection[Any]):
        self._connection = connection

    def cursor(self) -> Cursor:
        return Cursor(self._connection.cursor())

    def close(self) -> None:
        self._connection.close()

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()


def connect(**config: Any) -> Connection:
    """Accept legacy ``database`` config key and open a PostgreSQL connection."""
    postgres_config = dict(config)
    if "database" in postgres_config:
        postgres_config["dbname"] = postgres_config.pop("database")
    return Connection(psycopg.connect(**postgres_config))
