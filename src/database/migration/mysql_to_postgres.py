#!/usr/bin/env python3
"""Stream StockSearcher dataset tables from MySQL to the PostgreSQL stock DB.

Set the SOURCE_DB and TARGET_DB environment variables below before running this
script. The script reads MySQL rows with fetchmany(), commits one PostgreSQL
transaction per batch, and never truncates a target table.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

import mysql.connector
import psycopg


# Fill connection values through environment variables; do not put real passwords
# in this source file. The defaults describe the local Docker Compose topology.
SOURCE_DB = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", ""),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "stock"),
}
TARGET_DB = {
    "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("STOCK_DB_USER", "stock_app"),
    "password": os.getenv("STOCK_DB_PASSWORD", ""),
    "dbname": os.getenv("STOCK_DB_NAME", "stock"),
}

# MySQL DATETIME has no timezone. This default matches the existing Docker TZ;
# override MYSQL_SOURCE_TIME_ZONE if the source MySQL values use another zone.
SOURCE_TIME_ZONE = os.getenv("MYSQL_SOURCE_TIME_ZONE", "Asia/Seoul")
DEFAULT_BATCH_SIZE = int(os.getenv("MIGRATION_BATCH_SIZE", "5000"))


@dataclass(frozen=True)
class TableSpec:
    source_table: str
    target_table: str
    source_columns: tuple[str, ...]
    target_columns: tuple[str, ...]
    primary_key: tuple[str, ...]


STOCK_LIST_KR_COLUMNS = (
    "code", "name", "market", "order_no", "create_date", "update_date",
)
STOCK_LIST_JP_COLUMNS = (
    "code", "name", "stocktype", "industry33code", "industry33type",
    "industry17code", "industry17type", "scalecode", "scaletype",
    "create_date", "update_date",
)
STOCK_DATA_DAILY_COLUMNS = (
    "code", "date", "open", "high", "low", "close", "volume", "transamnt",
    "5mvavg", "20mvavg", "50mvavg", "60mvavg", "120mvavg", "240mvavg",
    "upperband60_1", "lowerband60_1", "lowerband60_3", "di_plus", "di_minus",
    "adx", "create_date", "update_date",
)
STOCK_DATA_WEEK_COLUMNS = (
    "code", "date", "open", "high", "low", "close", "volume", "transamnt",
    "5mvavg", "20mvavg", "50mvavg", "60mvavg", "upperband60_1",
    "lowerband60_1", "lowerband60_3", "di_plus", "di_minus", "adx",
    "create_date", "update_date",
)


TABLES = (
    TableSpec("STOCK_LIST_KR", "stock_list_kr", STOCK_LIST_KR_COLUMNS, STOCK_LIST_KR_COLUMNS, ("code",)),
    TableSpec("STOCK_LIST_JP", "stock_list_jp", STOCK_LIST_JP_COLUMNS, STOCK_LIST_JP_COLUMNS, ("code",)),
    TableSpec("STOCK_DATA_KR", "stock_data_kr", STOCK_DATA_DAILY_COLUMNS, STOCK_DATA_DAILY_COLUMNS, ("code", "date")),
    TableSpec("STOCK_DATA_WEEK_KR", "stock_data_week_kr", STOCK_DATA_WEEK_COLUMNS, STOCK_DATA_WEEK_COLUMNS, ("code", "date")),
    TableSpec("STOCK_DATA_JP", "stock_data_jp", STOCK_DATA_DAILY_COLUMNS, STOCK_DATA_DAILY_COLUMNS, ("code", "date")),
    TableSpec("STOCK_DATA_WEEK_JP", "stock_data_week_jp", STOCK_DATA_WEEK_COLUMNS, STOCK_DATA_WEEK_COLUMNS, ("code", "date")),
)
TABLE_BY_TARGET = {spec.target_table: spec for spec in TABLES}


def quote_identifier(identifier: str) -> str:
    """Quote a verified SQL identifier, including columns beginning with digits."""
    return '"' + identifier.replace('"', '""') + '"'


def required_connection_values(config: dict[str, Any], label: str) -> None:
    missing = [key for key in ("user", "password") if not config.get(key)]
    if missing:
        raise ValueError(f"{label} connection settings are missing: {', '.join(missing)}")


def source_select_sql(spec: TableSpec) -> str:
    columns = ", ".join(f"`{column}`" for column in spec.source_columns)
    order_by = ", ".join(f"`{column}`" for column in spec.primary_key)
    return f"SELECT {columns} FROM `{spec.source_table}` ORDER BY {order_by}"


def target_upsert_sql(spec: TableSpec) -> str:
    columns = ", ".join(quote_identifier(column) for column in spec.target_columns)
    placeholders = ", ".join(["%s"] * len(spec.target_columns))
    primary_key = ", ".join(quote_identifier(column) for column in spec.primary_key)
    updates = [
        f"{quote_identifier(column)} = EXCLUDED.{quote_identifier(column)}"
        for column in spec.target_columns
        if column not in spec.primary_key and column != "create_date"
    ]
    return (
        f"INSERT INTO {quote_identifier(spec.target_table)} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT ({primary_key}) DO UPDATE SET "
        + ", ".join(updates)
    )


def normalize_timestamp(value: Any, source_timezone: ZoneInfo) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=source_timezone)
    return value


def normalize_rows(rows: Iterable[Sequence[Any]], spec: TableSpec, source_timezone: ZoneInfo) -> list[tuple[Any, ...]]:
    timestamp_indexes = [
        index for index, column in enumerate(spec.target_columns)
        if column in {"create_date", "update_date"}
    ]
    normalized: list[tuple[Any, ...]] = []
    for row in rows:
        values = list(row)
        for index in timestamp_indexes:
            values[index] = normalize_timestamp(values[index], source_timezone)
        normalized.append(tuple(values))
    return normalized


def migrate_table(
    source_connection: mysql.connector.MySQLConnection,
    target_connection: psycopg.Connection[Any] | None,
    spec: TableSpec,
    batch_size: int,
    dry_run: bool,
    source_timezone: ZoneInfo,
) -> int:
    migrated = 0
    source_cursor = source_connection.cursor(buffered=False)
    try:
        source_cursor.execute(source_select_sql(spec))
        upsert_sql = target_upsert_sql(spec)
        while rows := source_cursor.fetchmany(batch_size):
            batch = normalize_rows(rows, spec, source_timezone)
            if not dry_run:
                assert target_connection is not None
                with target_connection.cursor() as target_cursor:
                    target_cursor.executemany(upsert_sql, batch)
                target_connection.commit()
            migrated += len(batch)
            print(f"{spec.target_table}: {migrated} rows processed", flush=True)
    except Exception:
        if target_connection is not None:
            target_connection.rollback()
        raise
    finally:
        source_cursor.close()
    return migrated


def table_count(connection: Any, table_name: str, mysql: bool) -> int:
    quoted_table = f"`{table_name}`" if mysql else quote_identifier(table_name)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
        return int(cursor.fetchone()[0])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate StockSearcher dataset tables from MySQL to PostgreSQL.")
    parser.add_argument("--tables", nargs="*", choices=tuple(TABLE_BY_TARGET), help="Target table names to migrate.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true", help="Read and validate batches without writing PostgreSQL rows.")
    parser.add_argument("--validate-counts", action="store_true", help="Print source and target row counts after each table.")
    parser.add_argument("--source-timezone", default=SOURCE_TIME_ZONE, help="IANA timezone for naive MySQL DATETIME values.")
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    if args.dry_run and args.validate_counts:
        parser.error("--validate-counts requires PostgreSQL writes; do not combine it with --dry-run")
    return args


def main() -> int:
    args = parse_args()
    required_connection_values(SOURCE_DB, "MySQL source")
    if not args.dry_run:
        required_connection_values(TARGET_DB, "PostgreSQL target")
    source_timezone = ZoneInfo(args.source_timezone)
    selected_names = set(args.tables) if args.tables else None
    selected = tuple(spec for spec in TABLES if selected_names is None or spec.target_table in selected_names)

    source_connection = mysql.connector.connect(**SOURCE_DB)
    target_connection = None if args.dry_run else psycopg.connect(**TARGET_DB)
    try:
        for spec in selected:
            print(f"Migrating {spec.source_table} -> {spec.target_table}", flush=True)
            migrated = migrate_table(
                source_connection, target_connection, spec, args.batch_size, args.dry_run, source_timezone
            )
            if args.validate_counts:
                source_count = table_count(source_connection, spec.source_table, mysql=True)
                target_count = table_count(target_connection, spec.target_table, mysql=False)
                print(f"{spec.target_table}: source={source_count}, target={target_count}, processed={migrated}")
                if target_count < source_count:
                    raise RuntimeError(f"Target row count is lower than source for {spec.target_table}")
    finally:
        source_connection.close()
        if target_connection is not None:
            target_connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
