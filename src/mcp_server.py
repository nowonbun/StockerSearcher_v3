"""Read-only Streamable HTTP MCP server for PostgreSQL stock data."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn


MARKETS = ("KR", "JP")


def _market(value: str) -> str:
    market = value.upper()
    if market not in MARKETS:
        raise ValueError("market must be KR or JP")
    return market


def _table(prefix: str, market: str) -> str:
    """Return an identifier selected only from fixed application table names."""
    return f"{prefix}_{_market(market).lower()}"


def _db_config() -> dict[str, Any]:
    required = ("STOCK_DB_HOST", "STOCK_DB_PORT", "STOCK_DB_NAME", "STOCK_DB_USER", "STOCK_DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required database environment variables: {', '.join(missing)}")
    return {
        "host": os.environ["STOCK_DB_HOST"],
        "port": int(os.environ["STOCK_DB_PORT"]),
        "dbname": os.environ["STOCK_DB_NAME"],
        "user": os.environ["STOCK_DB_USER"],
        "password": os.environ["STOCK_DB_PASSWORD"],
    }


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with psycopg.connect(**_db_config(), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [{key: _normalize(value) for key, value in row.items()} for row in cursor.fetchall()]


def _stock_data(market: str, code: str, limit: int, start_date: str | None, end_date: str | None, *, weekly: bool) -> list[dict[str, Any]]:
    if not code:
        raise ValueError("code is required")
    table = _table("stock_data_week" if weekly else "stock_data", market)
    clauses = ["code = %s"]
    params: list[Any] = [code]
    if start_date:
        clauses.append("date >= %s")
        params.append(start_date)
    if end_date:
        clauses.append("date <= %s")
        params.append(end_date)
    query = f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} ORDER BY date DESC"
    if limit > 0:
        query += " LIMIT %s"
        params.append(limit)
    return _rows(query, tuple(params))


mcp = FastMCP("stocksearcher-postgres-mcp", streamable_http_path="/")


@mcp.tool()
def list_stocks(market: str = "KR") -> list[dict[str, Any]]:
    """Return a market's stock codes and names, ordered by name then code."""
    return _rows(f"SELECT code, name FROM {_table('stock_list', market)} ORDER BY name, code")


@mcp.tool()
def stock_data(market: str, code: str, limit: int = 2000, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Return daily OHLCV and indicator rows for a stock, newest first."""
    return _stock_data(market, code, limit, start_date, end_date, weekly=False)


@mcp.tool()
def stock_data_week(market: str, code: str, limit: int = 500, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Return weekly OHLCV and indicator rows for a stock, newest first."""
    return _stock_data(market, code, limit, start_date, end_date, weekly=True)


@mcp.tool()
def list_predict_dates(market: str, limit: int = 120, weekly: bool = False) -> list[str]:
    """Return prediction cutoff dates, newest first."""
    table = _table("stock_predict_week" if weekly else "stock_predict", market)
    query = f"SELECT DISTINCT data_cutoff FROM {table} ORDER BY data_cutoff DESC"
    params: tuple[Any, ...] = ()
    if limit > 0:
        query += " LIMIT %s"
        params = (limit,)
    return [str(row["data_cutoff"]) for row in _rows(query, params)]


@mcp.tool()
def predict_rows(market: str, as_of: str, weekly: bool = False) -> list[dict[str, Any]]:
    """Return all prediction rows for a cutoff date, ordered by probability."""
    if not as_of:
        raise ValueError("as_of is required")
    table = _table("stock_predict_week" if weekly else "stock_predict", market)
    list_table = _table("stock_list", market)
    return _rows(
        f"SELECT p.*, s.name FROM {table} p JOIN {list_table} s ON s.code = p.code "
        "WHERE p.data_cutoff = %s ORDER BY p.probability DESC",
        (as_of,),
    )


def create_asgi_app() -> Starlette:
    """Expose FastMCP's root transport below the public /mcp endpoint."""
    mcp_app = mcp.streamable_http_app()
    lifespan = getattr(mcp_app, "lifespan", None)
    if lifespan is None and hasattr(mcp_app, "router"):
        lifespan = mcp_app.router.lifespan_context
    return Starlette(routes=[Mount("/mcp", app=mcp_app)], lifespan=lifespan)


def main() -> None:
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    uvicorn.run(create_asgi_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
