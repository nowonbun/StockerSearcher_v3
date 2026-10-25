"""Read-only Streamable HTTP MCP server for PostgreSQL stock data."""

from __future__ import annotations

import datetime as dt
import math
import os
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
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


def _allowed_mcp_hosts() -> list[str]:
    """Return the configured public hosts plus local hosts for the active port."""
    port = os.getenv("MCP_PORT", "8000")
    configured = [
        host.strip()
        for host in os.getenv("MCP_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    return list(dict.fromkeys([*configured, f"127.0.0.1:{port}", f"localhost:{port}"]))


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
    # 웹·추천과 같은 분리 데이터 뷰를 사용해 구형 테이블의 수집 공백을 피한다.
    table = _table("stock_data_split_week" if weekly else "stock_data_split", market)
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


mcp = FastMCP(
    "stocksearcher-postgres-mcp",
    instructions=(
        "한국·일본 주식의 저장된 데이터를 조회하는 읽기 전용 MCP입니다. "
        "처음 사용할 때 readme()로 용도와 제약을, usage()로 도구별 입력과 예시를 확인하세요. "
        "후보 목록은 recommend_stocks로 조회하며 거래대금 순위이지 상승 확률이 아닙니다. "
        "실시간 시세나 수익률을 보장하지 않으며 응답의 기준일을 확인해야 합니다."
    ),
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_mcp_hosts(),
    ),
)


@mcp.tool()
def readme() -> dict[str, Any]:
    """이 MCP의 정체성·지원 시장·용도·제약을 DB 연결 없이 확인한다. 처음 사용할 때 호출한다."""
    return {
        "server": "stocksearcher-postgres-mcp",
        "purpose": "PostgreSQL stock DB에 저장된 한국·일본 종목, 일봉·주봉 및 웹 볼린저 스캐너 후보 조회",
        "read_only": True,
        "markets": list(MARKETS),
        "limitations": (
            "실시간 시세 제공 서버가 아니다. 저장된 데이터의 날짜를 확인해야 하며 최신성을 가정하면 안 된다. "
            "추천 후보는 볼린저밴드 조건과 거래대금 순위이며 일목균형표나 모델 예측·상승 확률이 아니다. "
            "수익률을 보장하지 않으며 DB 변경·주문·매매 실행 도구는 제공하지 않는다."
        ),
        "next_step": "usage()로 전체 사용법을 확인하거나 usage(tool='recommend_stocks')로 후보 조회 사용법을 확인한다.",
    }


@mcp.tool()
async def usage(tool: str | None = None) -> dict[str, Any]:
    """도구별 설명·실제 입력 스키마·호출 예시·주의사항을 DB 연결 없이 확인한다.

    tool 생략 시 전체 도구, 지정 시 해당 도구만 반환한다. 예: tool='recommend_stocks'.
    """
    examples: dict[str, list[dict[str, Any]]] = {
        "readme": [{}],
        "usage": [{}, {"tool": "recommend_stocks"}],
        "list_stocks": [{"market": "KR"}, {"market": "JP"}],
        "stock_data": [{"market": "KR", "code": "005930", "limit": 20, "start_date": "2026-10-01", "end_date": "2026-10-23"}],
        "stock_data_week": [{"market": "JP", "code": "7203", "limit": 20}],
        "recommend_stocks": [
            {"market": "KR", "date": "2026-10-23", "mode": "upper", "weekly": False, "limit": 20},
            {"market": "JP", "date": "2026-10-23", "mode": "lower", "weekly": True, "trans_amnt_min": 2500000000, "close_max": 1000, "limit": 10},
        ],
    }
    registered = await mcp.list_tools()
    if tool is not None and tool not in {entry.name for entry in registered}:
        raise ValueError("Unknown tool. Call usage() to list available tools.")
    return {
        "tools": {
            entry.name: {"description": entry.description, "parameters": entry.inputSchema, "examples": examples[entry.name]}
            for entry in registered if tool is None or entry.name == tool
        },
        "notes": {
            "general": "예시 날짜는 호출 형식 설명용이며 데이터 존재를 보장하지 않는다. KR 금액은 원, JP 금액은 엔이다. 데이터는 최신성 보장 없이 저장된 값만 조회한다.",
            "list_stocks": "market 기본값 KR. 종목명·코드 순으로 반환한다.",
            "stock_data": "market·code 필수. 웹·추천과 같은 일봉 분리 데이터 뷰를 조회한다. 날짜 범위는 양 끝을 포함한다. 최신 날짜부터 반환하며 limit 기본 2000, 0 이하면 제한이 없다.",
            "stock_data_week": "market·code 필수. 웹·추천과 같은 주봉 분리 데이터 뷰를 조회한다. 날짜 범위는 양 끝을 포함한다. 최신 날짜부터 반환하며 limit 기본 500, 0 이하면 제한이 없다.",
            "recommend_stocks": (
                "market 기본 KR, weekly 기본 false, mode 기본 upper, limit 기본 20(1~500). "
                "date는 YYYY-MM-DD이며 해당 날짜와 정확히 일치하는 데이터만 조회한다. 주봉도 저장된 주봉 날짜와 일치해야 한다. "
                "date 생략 시 최근 30일 내 두 번째 최신 데이터 날짜를 사용하고, 한 개면 해당 날짜, 없으면 빈 목록이다. "
                "upper는 종가 > 볼린저 상단, lower는 볼린저 하단 < 종가 < 60기간 이동평균이다. "
                "거래대금 > trans_amnt_min, 종가 < close_max를 적용한다. "
                "기본 거래대금 하한: 일봉 KR 10억 원·JP 5억 엔, 주봉 KR 50억 원·JP 25억 엔. 기본 종가 상한: KR 10만 원·JP 1000엔. "
                "JP는 ETF・ETN을 제외한다. 거래대금 내림차순이며 동률 순서는 보장하지 않는다. "
                "반환 메타데이터의 date·필터와 rows를 확인한다. rows는 rank·종목·종가·거래대금·밴드·비율·선정 근거를 포함한다. "
                "빈 목록은 데이터 부재 또는 조건 미충족일 수 있으며 DB 오류는 빈 목록으로 대체하지 않는다."
            ),
        },
    }


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
def recommend_stocks(
    market: str = "KR",
    date: str | None = None,
    weekly: bool = False,
    mode: str = "upper",
    trans_amnt_min: float | None = None,
    close_max: float | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """웹 볼린저 스캐너와 같은 조건의 후보를 거래대금 내림차순으로 반환한다.

    market은 KR/JP, weekly는 주봉 여부, mode는 upper(상단 돌파) 또는
    lower(하단과 60기간 이동평균 사이)다. date는 YYYY-MM-DD이며 생략 시
    웹처럼 최근 30일 내 두 번째 최신 데이터 날짜(한 개면 그 날짜)를 쓴다.
    필터 생략 시 웹의 시장별·주기별 기본값을 사용한다. limit은 1~500이다.
    모델 예측이나 수익률 보장이 아닌 규칙 기반 조회다.
    """
    market = _market(market)
    if mode not in ("upper", "lower"):
        raise ValueError("mode must be upper or lower")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")
    if date is not None:
        try:
            if dt.date.fromisoformat(date).isoformat() != date:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("date must be a valid YYYY-MM-DD date") from None
    for name, value in (("trans_amnt_min", trans_amnt_min), ("close_max", close_max)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError(f"{name} must be finite and numeric")
    if trans_amnt_min is None:
        trans_amnt_min = (2500000000 if weekly else 500000000) if market == "JP" else (5000000000 if weekly else 1000000000)
    if close_max is None:
        close_max = 1000 if market == "JP" else 100000
    table = _table("stock_data_split_week" if weekly else "stock_data_split", market)
    list_table = _table("stock_list", market)
    if date is None:
        dates = _rows(f"SELECT DISTINCT date FROM {table} WHERE date >= CURRENT_DATE - INTERVAL '30 days' ORDER BY date DESC LIMIT 2")
        if dates:
            date = str(_normalize(dates[1 if len(dates) > 1 else 0]["date"]))
    result: dict[str, Any] = {
        "market": market, "date": date, "weekly": weekly, "mode": mode,
        "trans_amnt_min": trans_amnt_min, "close_max": close_max,
        "limit": limit, "sort_by": "transamnt_desc", "rows": [],
    }
    if date is None:
        return result
    lower = mode == "lower"
    band = "lowerband60_1" if lower else "upperband60_1"
    condition = 'sd.lowerband60_1 < sd.close AND sd.close < sd."60mvavg"' if lower else 'sd.close > sd.upperband60_1'
    etf = " AND sl.stocktype != 'ETF・ETN'" if market == "JP" else ""
    rows = _rows(
        f'SELECT sd.date, sd.code, sl.name, sd.close, sd.{band} AS band, sd."60mvavg" AS ma60, '
        f'sd.transamnt AS "transAmnt", (sd.close / NULLIF(sd.{band}, 0)) AS ratio '
        f"FROM {table} sd JOIN {list_table} sl ON sl.code = sd.code "
        f"WHERE sd.date = %s AND {condition} "
        f"AND sd.transamnt > %s AND sd.close < %s{etf} "
        "ORDER BY sd.transamnt DESC LIMIT %s",
        (date, trans_amnt_min, close_max, limit),
    )
    reason = "볼린저 하단 < 종가 < 60기간 이동평균" if lower else "종가 > 볼린저 상단"
    for rank, row in enumerate(rows, 1):
        candidate = {
            "rank": rank, "date": _normalize(row["date"]), "code": row["code"],
            "name": row["name"], "close": _normalize(row["close"]),
            band: _normalize(row["band"]), "transAmnt": _normalize(row["transAmnt"]),
            f"{mode}band_ratio": _normalize(row["ratio"]),
            "reason": f"{reason}; 거래대금 > {trans_amnt_min}; 종가 < {close_max}; 거래대금 내림차순",
        }
        if lower:
            candidate["ma60"] = _normalize(row["ma60"])
        result["rows"].append(candidate)
    return result


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
