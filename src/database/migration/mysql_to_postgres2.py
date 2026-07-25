#!/usr/bin/env python3
"""Migrate only StockSearcher prediction tables from MySQL to PostgreSQL.

Required environment variables:
MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
POSTGRES_HOST, STOCK_DB_USER, STOCK_DB_PASSWORD, STOCK_DB_NAME.
MYSQL_PORT and POSTGRES_PORT default to 3306 and 5432. The script upserts rows,
never truncates a target table, and preserves the original ``created_at`` value.
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


SOURCE_DB = {
    "host": os.getenv("MYSQL_HOST", ""),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", ""),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", ""),
}
TARGET_DB = {
    "host": os.getenv("POSTGRES_HOST", ""),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("STOCK_DB_USER", ""),
    "password": os.getenv("STOCK_DB_PASSWORD", ""),
    "dbname": os.getenv("STOCK_DB_NAME", ""),
}
SOURCE_TIME_ZONE = os.getenv("MYSQL_SOURCE_TIME_ZONE", "Asia/Seoul")
DEFAULT_BATCH_SIZE = int(os.getenv("MIGRATION_BATCH_SIZE", "5000"))
PREDICTION_COLUMNS = (
    "data_cutoff", "code", "probability", "run_name", "seq_len",
    "horizon_days", "rise_threshold", "created_at",
)
PREDICTION_PRIMARY_KEY = ("data_cutoff", "code", "run_name")


@dataclass(frozen=True)
class TableSpec:
    source_table: str
    target_table: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]


TABLES = (
    TableSpec("STOCK_PREDICT_JP", "stock_predict_jp", PREDICTION_COLUMNS, PREDICTION_PRIMARY_KEY),
    TableSpec("STOCK_PREDICT_KR", "stock_predict_kr", PREDICTION_COLUMNS, PREDICTION_PRIMARY_KEY),
    TableSpec("STOCK_PREDICT_WEEK_JP", "stock_predict_week_jp", PREDICTION_COLUMNS, PREDICTION_PRIMARY_KEY),
    TableSpec("STOCK_PREDICT_WEEK_KR", "stock_predict_week_kr", PREDICTION_COLUMNS, PREDICTION_PRIMARY_KEY),
)
TABLE_BY_TARGET = {spec.target_table: spec for spec in TABLES}


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def required_connection_values(config: dict[str, Any], label: str) -> None:
    missing = [key for key in ("host", "user", "password") if not config.get(key)]
    database_key = "dbname" if "dbname" in config else "database"
    if not config.get(database_key):
        missing.append(database_key)
    if missing:
        raise ValueError(f"{label} connection settings are missing: {', '.join(missing)}")


def source_select_sql(spec: TableSpec) -> str:
    columns = ", ".join(f"`{column}`" for column in spec.columns)
    order_by = ", ".join(f"`{column}`" for column in spec.primary_key)
    return f"SELECT {columns} FROM `{spec.source_table}` ORDER BY {order_by}"


def target_upsert_sql(spec: TableSpec) -> str:
    columns = ", ".join(quote_identifier(column) for column in spec.columns)
    placeholders = ", ".join(["%s"] * len(spec.columns))
    primary_key = ", ".join(quote_identifier(column) for column in spec.primary_key)
    updates = ", ".join(
        f"{quote_identifier(column)} = EXCLUDED.{quote_identifier(column)}"
        for column in spec.columns
        if column not in spec.primary_key and column != "created_at"
    )
    return (
        f"INSERT INTO {quote_identifier(spec.target_table)} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT ({primary_key}) DO UPDATE SET {updates}"
    )


def normalize_rows(rows: Iterable[Sequence[Any]], spec: TableSpec, source_timezone: ZoneInfo) -> list[tuple[Any, ...]]:
    created_at_index = spec.columns.index("created_at")
    normalized: list[tuple[Any, ...]] = []
    for row in rows:
        values = list(row)
        created_at = values[created_at_index]
        if isinstance(created_at, datetime) and created_at.tzinfo is None:
            values[created_at_index] = created_at.replace(tzinfo=source_timezone)
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
        while batch_rows := source_cursor.fetchmany(batch_size):
            batch = normalize_rows(batch_rows, spec, source_timezone)
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
    parser = argparse.ArgumentParser(description="Migrate only prediction tables from MySQL to PostgreSQL.")
    parser.add_argument("--tables", nargs="*", choices=tuple(TABLE_BY_TARGET), help="Target prediction tables to migrate.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true", help="Read and normalize rows without PostgreSQL writes.")
    parser.add_argument("--validate-counts", action="store_true", help="Print source and target row counts after each migrated table.")
    parser.add_argument("--source-timezone", default=SOURCE_TIME_ZONE, help="IANA timezone for naive MySQL created_at values.")
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
            migrated = migrate_table(source_connection, target_connection, spec, args.batch_size, args.dry_run, source_timezone)
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
