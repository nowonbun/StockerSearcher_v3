"""
일본 주식 데이터 수집/가공/저장을 담당하는 리팩토링 버전.

- 기능 유지: 종목 목록 저장, 일/주봉 데이터 수집, 지표 계산, DB 저장
- 개선 사항:
  - 모듈 최상단으로 import 정리 및 타입 힌트 추가
  - 중복 로직 제거: 일/주 공통 처리 함수로 통합
  - 이동평균/볼린저 계산 경계 처리 명확화(데이터 부족 시 0 반환)
  - DB 입력시 안전한 파라미터 바인딩(executemany) 사용
  - 드라이버 자원 정리 보장
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from io import BytesIO
from typing import Any, Iterable, List, Optional, Sequence, Tuple

_JST = timezone(timedelta(hours=9))

import psycopg
import pandas as pd
import requests
import os

import entity.stock_list_node as stock_list_node
import entity.stock_models as stock_models
import function.common as common
import function.static as static
import function.stock_lib as stock_lib


# ----------------------------
# logging
# ----------------------------
_LOGGER = None


def _log(msg: str) -> None:
    global _LOGGER
    if _LOGGER is not None:
        common.write_log(_LOGGER, msg)
    else:
        print(msg)


# ----------------------------
# 종목 목록 관련
# ----------------------------
def get_stock_list_by_url(url: str, timeout: int = 30) -> pd.DataFrame:
    """JPX에서 공개하는 종목 목록을 다운로드해 DataFrame으로 반환.

    실패 시 예외를 발생시킨다.
    """
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return pd.read_excel(BytesIO(resp.content))


def save_stock_list(db_config: dict) -> List[stock_list_node.StockListNode]:
    """종목 목록을 다운로드 후 DB(STOCK_LIST_JP)에 upsert 저장하고 목록을 반환."""
    # https://www.jpx.co.jp/markets/statistics-equities/misc/01.html
    df = get_stock_list_by_url(
        "https://www.jpx.co.jp//markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    )

    stocks: List[stock_list_node.StockListNode] = [
        stock_list_node.StockListNode(*row.tolist())
        for _, row in df.iterrows()
        if row.get("stocktype", "") not in ("ETF／ETN", "PRO Market", "ETF�ETN")
    ]

    # 기존 프로젝트 스타일을 존중: 문자열 쿼리 생성 후 단일 실행
    query, payload = stock_list_node.generateSqlQuery(stocks, table_name="STOCK_LIST_JP")
    common.execute_many(db_config, query, payload)
    _log("STOCK_LIST_JP 저장 완료")
    return stocks


# ----------------------------
# 시세 데이터 가공
# ----------------------------
def _moving_average(series: Sequence[float], idx: int, window: int) -> float:
    """원본 로직과 동일한 부분창 평균: 데이터가 부족해도 분모는 고정(window)."""
    start = max(0, idx - (window - 1))
    s = series[start : idx + 1]
    return float(sum(s) / window)


def _bollinger_bands(series: Sequence[float], idx: int, window: int, k: float) -> Tuple[float, float, float]:
    """원본과 동일하게 가용 구간으로 계산. 표본이 1개면 표준편차 0 처리."""
    import statistics

    start = max(0, idx - (window - 1))
    w = series[start : idx + 1]
    mean = statistics.mean(w)
    std = float(statistics.stdev(w)) if len(w) >= 2 else 0.0
    upper = mean + std * k
    lower = mean - std * k
    return float(upper), float(mean), float(lower)


def _calculate_dmi(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 20,
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """Return DI+, DI-, ADX lists aligned to input length (SMA-based)."""
    n = len(closes)
    if n == 0:
        return [], [], []

    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    di_plus: List[Optional[float]] = [None] * n
    di_minus: List[Optional[float]] = [None] * n
    dx: List[Optional[float]] = [None] * n

    for i in range(period, n):
        start = i - period + 1
        tr_sum = sum(tr[start : i + 1])
        if tr_sum == 0:
            continue
        plus_sum = sum(plus_dm[start : i + 1])
        minus_sum = sum(minus_dm[start : i + 1])
        di_plus[i] = 100.0 * plus_sum / tr_sum
        di_minus[i] = 100.0 * minus_sum / tr_sum
        denom = (di_plus[i] or 0.0) + (di_minus[i] or 0.0)
        if denom != 0:
            dx[i] = 100.0 * abs((di_plus[i] or 0.0) - (di_minus[i] or 0.0)) / denom

    adx: List[Optional[float]] = [None] * n
    first_adx_idx = (period * 2) - 1
    for i in range(first_adx_idx, n):
        start = i - period + 1
        window = dx[start : i + 1]
        if any(v is None for v in window):
            continue
        adx[i] = sum(v for v in window if v is not None) / period

    return di_plus, di_minus, adx


def _filter_valid(series: stock_models.StockSeries) -> stock_models.StockSeries:
    """None 값이 포함되거나 Volume이 0인 캔들을 제거."""
    return stock_models.StockSeries(
        c for c in series.candles
        if None not in (c.open, c.high, c.low, c.close, c.volume) and float(c.volume) > 0
    )


def _ts_to_date(ts_ms: int, normalize_to_monday: bool = False) -> str:
    """밀리초 타임스탬프를 JST 기준 날짜 문자열로 변환.

    normalize_to_monday=True 이면 해당 주의 월요일 날짜를 반환(주봉 정규화용).
    Yahoo Finance JP 주봉의 타임스탬프는 '월요일 00:00 JST = 일요일 15:00 UTC'이므로
    UTC 환경에서 fromtimestamp()를 쓰면 일요일로 저장됨 → JST 고정으로 방지.
    진행 중인 주의 봉은 실행 날짜 타임스탬프를 쓰므로 월요일 정규화로 중복 방지.
    """
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=_JST)
    if normalize_to_monday:
        dt = dt - timedelta(days=dt.weekday())
    return dt.strftime("%Y-%m-%d")


def build_calculated_rows(
    raw: dict[str, List[Any]], allow_long_ma_null: bool = False,
    normalize_to_monday: bool = False,
) -> List[List[Any]]:
    """지표 계산을 적용한 행 단위 데이터 생성.

    반환 스키마(헤더 없음):
    [Date, Open, High, Low, Close, Volume, TranAmnt,
     5MvAvg, 20MvAvg, 50MvAvg, 60MvAvg, 120MvAvg, 240MvAvg,
     UpperBand60_1, LowerBand60_1, LowerBand60_3, DI_plus, DI_minus, ADX]
    """
    series = stock_models.StockSeries.from_raw(raw)
    series = _filter_valid(series)
    if len(series) == 0:
        return []

    ts = [c.timestamp for c in series.candles]
    op = [float(c.open) for c in series.candles]
    hi = [float(c.high) for c in series.candles]
    lo = [float(c.low) for c in series.candles]
    cl = [float(c.close) for c in series.candles]
    vo = [float(c.volume) for c in series.candles]

    di_plus, di_minus, adx = _calculate_dmi(hi, lo, cl, period=20)

    rows: List[List[Any]] = []
    for i in range(len(ts)):
        avg5 = _moving_average(cl, i, 5)
        avg20 = _moving_average(cl, i, 20)
        avg50 = _moving_average(cl, i, 50)
        avg60 = _moving_average(cl, i, 60)
        avg120 = None if allow_long_ma_null and i < 119 else _moving_average(cl, i, 120)
        avg240 = None if allow_long_ma_null and i < 239 else _moving_average(cl, i, 240)

        # 원본 로직과 동일: 0인 경우만 스킵
        if (
            avg5 == 0
            or avg20 == 0
            or avg50 == 0
            or avg60 == 0
            or (avg120 == 0 if avg120 is not None else False)
            or (avg240 == 0 if avg240 is not None else False)
        ):
            continue

        up60_1, mid60_1, lo60_1 = _bollinger_bands(cl, i, 60, 1)
        up60_3, mid60_3, lo60_3 = _bollinger_bands(cl, i, 60, 3)

        # 이동평균 구간이 충분하면 볼린저도 충분하므로 0 체크는 생략
        rows.append(
            [
                _ts_to_date(ts[i], normalize_to_monday),
                round(op[i]),
                round(hi[i]),
                round(lo[i]),
                round(cl[i]),
                round(vo[i]),
                round(cl[i] * vo[i]),
                round(avg5),
                round(avg20),
                round(avg50),
                round(avg60),
                round(avg120) if avg120 is not None else None,
                round(avg240) if avg240 is not None else None,
                round(up60_1),
                round(lo60_1),
                round(lo60_3),
                round(di_plus[i]) if di_plus[i] is not None else None,
                round(di_minus[i]) if di_minus[i] is not None else None,
                round(adx[i]) if adx[i] is not None else None,
            ]
        )
    return rows


# ----------------------------
# 원천 데이터 수집
# ----------------------------
def fetch_stock_raw(
    driver: Any,
    symbol: str,
    period_type: str,
    period: int,
    frequency_type: str,
    frequency: int,
    retries: int = 3,
) -> Optional[dict]:
    """야후 파이낸스 차트 API(셀레니움 기반)에서 원시 캔들 데이터 dict 반환."""
    for i in range(retries):
        try:
            lib = stock_lib.StockLib(symbol)
            data = lib.get_historical(driver, period_type, period, frequency_type, frequency)
            if data is None:
                _log(f"{symbol} 데이터 없음")
                return None
            return data
        except requests.Timeout:
            _log(f"{symbol} timeout")
        except requests.RequestException as e:
            _log(f"error - {e}")
        _log(f"retry{i} - {symbol}")
    return None


# ----------------------------
# DB 저장
# ----------------------------
def _build_insert_query(
    table: str, include_lowerband60_3: bool, fast: bool = False, include_long_ma: bool = True
) -> Tuple[str, int]:
    """파라미터 바인딩용 INSERT 쿼리 생성.

    fast=True: ON CONFLICT DO NOTHING (벌크 초기 적재용)
    fast=False: ON CONFLICT DO UPDATE (기본 upsert)
    include_long_ma=False: 120mvavg/240mvavg 제외 (주봉 테이블 — 해당 컬럼 없음)

    반환: (query, value_count)
    """
    cols = [
        "code",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "transamnt",
        "5mvavg",
        "20mvavg",
        "50mvavg",
        "60mvavg",
    ]
    if include_long_ma:
        cols.extend(["120mvavg", "240mvavg"])
    cols.extend(["upperband60_1", "lowerband60_1"])
    if include_lowerband60_3:
        cols.append("lowerband60_3")
    cols.extend(["di_plus", "di_minus", "adx"])

    def sql_column(column: str) -> str:
        normalized = column.lower()
        return f'"{normalized}"' if normalized[0].isdigit() else normalized

    sql_cols = [sql_column(column) for column in cols]
    placeholders = ",".join(["%s"] * len(cols))
    insert_cols = ", ".join(sql_cols) + ", create_date, update_date"

    if fast:
        query = (
            f"INSERT INTO {table} ({insert_cols}) VALUES ("
            f"{placeholders}, now(), now()) ON CONFLICT (code, date) DO NOTHING"
        )
    else:
        query = (
            f"INSERT INTO {table} ({insert_cols}) VALUES ("
            f"{placeholders}, now(), now()) "
            "ON CONFLICT (code, date) DO UPDATE SET "
            + ", ".join([f"{sql_column(c)} = EXCLUDED.{sql_column(c)}" for c in cols[2:]])
            + ", update_date = now()"
        )
    return query, len(cols)


def insert_rows(
    table: str,
    code: str,
    rows: List[List[Any]],
    db_config: dict,
    include_lowerband60_3: bool,
    fast: bool = False,
    include_long_ma: bool = True,
) -> None:
    if not rows:
        _log(f"{code} 저장할 데이터 없음")
        return

    query, value_count = _build_insert_query(table, include_lowerband60_3, fast, include_long_ma)

    # r 레이아웃: [date, open, high, low, close, vol, transamnt,
    #              5mv(7), 20mv(8), 50mv(9), 60mv(10), 120mv(11), 240mv(12),
    #              upband(13), loband1(14), loband3(15), di+(16), di-(17), adx(18)]
    payload: List[Tuple[Any, ...]] = []
    for r in rows:
        base = [code] + r[:11]  # date..60mvavg
        if include_long_ma:
            base.extend(r[11:13])  # 120mvavg, 240mvavg
        base.extend(r[13:15])  # upperband60_1, lowerband60_1
        if include_lowerband60_3:
            base.append(r[15])  # lowerband60_3
        base.extend(r[16:19])  # di_plus, di_minus, adx
        payload.append(tuple(base))

    conn = psycopg.connect(**db_config)
    try:
        with conn.cursor() as cur:
            cur.executemany(query, payload)
        conn.commit()
        _log(f"{code} {table} {len(payload)}건 저장")
    except Exception as e:
        conn.rollback()
        _log(str(e))
        raise
    finally:
        conn.close()


# ----------------------------
# 파이프라인
# ----------------------------
def process_symbol(
    driver: Any,
    code: str,
    period: int,
    db_config: dict,
    freq_type: str,
    table: str,
    include_lowerband60_3: bool,
    fast: bool = False,
) -> None:
    raw = fetch_stock_raw(
        driver,
        f"{code}.T",
        stock_lib.PERIOD_TYPE_YEAR,
        period,
        freq_type,
        1,
    )
    if raw is None:
        _log(f"{code} 데이터 수집 실패")
        return
    is_weekly = freq_type == stock_lib.FREQUENCY_TYPE_WEEK
    rows = build_calculated_rows(raw, allow_long_ma_null=is_weekly, normalize_to_monday=is_weekly)
    insert_rows(table, code, rows, db_config, include_lowerband60_3, fast, include_long_ma=not is_weekly)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-week", action="store_true", help="주봉 수집 건너뜀")
    parser.add_argument(
        "--fast-insert",
        action="store_true",
        help="ON CONFLICT DO NOTHING (벌크 초기 적재용)",
    )
    args = parser.parse_args()
    include_week = not args.no_week
    fast = args.fast_insert

    global _LOGGER
    common.check_directory(static.dir)
    common.check_directory(os.path.join(static.dir, "log"))
    _LOGGER = common.setup_custom_logger(static.dir, "create_stock_dataset_jp")
    # 종목 목록 저장 및 시세 저장 엔트리포인트
    from selenium import webdriver
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    if os.getenv("CHROME_HEADLESS", "1") == "1":
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
    chromedriver_log_path = os.getenv(
        "CHROMEDRIVER_LOG_PATH",
        os.path.join(static.dir, "log", "chromedriver_jp.log"),
    )
    if os.path.exists(chromedriver_path):
        service = Service(
            chromedriver_path,
            service_args=["--verbose"],
            log_output=chromedriver_log_path,
        )
    else:
        service = Service(
            ChromeDriverManager().install(),
            service_args=["--verbose"],
            log_output=chromedriver_log_path,
        )
    driver = webdriver.Chrome(service=service, options=options)
    try:
        stocks = save_stock_list(static.db_config_jp)

        for s in stocks:
            code = s.code
            try:
                # 일봉
                process_symbol(
                    driver,
                    code,
                    static.period,
                    static.db_config_jp,
                    stock_lib.FREQUENCY_TYPE_DAY,
                    "STOCK_DATA_JP",
                    True,  # lowerband60_3 포함
                    fast,
                )
                if include_week:
                    # 주봉
                    process_symbol(
                        driver,
                        code,
                        static.period,
                        static.db_config_jp,
                        stock_lib.FREQUENCY_TYPE_WEEK,
                        "STOCK_DATA_WEEK_JP",
                        False,  # weekly 테이블은 lowerband60_3 미포함(기존 동작 준수)
                        fast,
                    )
            except Exception as e:
                _log(str(e))
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
