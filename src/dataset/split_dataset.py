"""Split OHLCV collection and technical-indicator calculation batches.

The legacy ``stock_data_*`` tables and collectors are intentionally not used or
modified here.  These batches write only the new ``stock_ohlcv_*`` and
``stock_indicator_*`` tables.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from calendar import monthrange
from typing import Any, Iterable

import FinanceDataReader as fdr
import pandas as pd
import psycopg

import entity.stock_models as stock_models
import function.static as static
import function.stock_lib as stock_lib
from dataset import dataset_jp, dataset_kr


@dataclass(frozen=True)
class TableSet:
    ohlcv: str
    indicators: str
    stock_list: str


TABLES = {
    ("KR", False): TableSet("stock_ohlcv_kr", "stock_indicator_kr", "stock_list_kr"),
    ("KR", True): TableSet("stock_ohlcv_week_kr", "stock_indicator_week_kr", "stock_list_kr"),
    ("JP", False): TableSet("stock_ohlcv_jp", "stock_indicator_jp", "stock_list_jp"),
    ("JP", True): TableSet("stock_ohlcv_week_jp", "stock_indicator_week_jp", "stock_list_jp"),
}


def _tables(market: str, weekly: bool) -> TableSet:
    try:
        return TABLES[(market.upper(), weekly)]
    except KeyError as error:
        raise ValueError(f"Unsupported market: {market}") from error


def _months_before(day: date, months: int) -> date:
    """Return the same calendar day N months earlier, clamped to month end."""
    if months <= 0:
        raise ValueError("months must be greater than zero")
    year, month = divmod((day.year * 12 + day.month - 1) - months, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def recent_collection_window(today: date | None = None, months: int | None = None) -> tuple[str, str]:
    """Return the configured inclusive calendar-month collection window."""
    end_date = today or date.today()
    start_date = _months_before(end_date, months or static.split_collection_months)
    return start_date.isoformat(), end_date.isoformat()


def _upsert_ohlcv(market: str, weekly: bool, code: str, rows: Iterable[tuple[Any, ...]]) -> None:
    payload = [(code, *row) for row in rows]
    if not payload:
        return
    table = _tables(market, weekly).ohlcv
    query = f"""
        INSERT INTO {table} (code, date, open, high, low, close, volume, create_date, update_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
        ON CONFLICT (code, date) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, volume = EXCLUDED.volume, update_date = now()
    """
    with psycopg.connect(**static.db_config) as connection, connection.cursor() as cursor:
        cursor.executemany(query, payload)


def _kr_rows(frame: pd.DataFrame, weekly: bool) -> list[tuple[Any, ...]]:
    if frame is None or frame.empty:
        return []
    required_columns = ("Open", "High", "Low", "Close", "Volume")
    missing_columns = [column for column in required_columns if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"KR OHLCV frame is missing required columns: {', '.join(missing_columns)}")
    work = frame[list(required_columns)].dropna().copy()
    if weekly:
        work = work.resample("W-FRI").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        ).dropna(subset=["Close"])
    return [
        (timestamp.strftime("%Y-%m-%d"), *(round(float(row[column])) for column in ("Open", "High", "Low", "Close", "Volume")))
        for timestamp, row in work.iterrows()
        if float(row["Volume"]) > 0
    ]


def collect_kr(weekly: bool = True) -> None:
    """Collect KR OHLCV only; no technical values are calculated in this batch."""
    dataset_kr.save_stock_list()
    codes = [row[0] for row in dataset_kr.get_stock_list()]
    start_date, end_date = recent_collection_window()

    def collect(code: str) -> None:
        frame = fdr.DataReader(code, start_date, end_date)
        _upsert_ohlcv("KR", False, code, _kr_rows(frame, False))
        if weekly:
            _upsert_ohlcv("KR", True, code, _kr_rows(frame, True))

    with ThreadPoolExecutor(max_workers=5) as executor:
        for _ in executor.map(collect, codes):
            pass


def _jp_rows(raw: dict[str, Any], weekly: bool) -> list[tuple[Any, ...]]:
    series = dataset_jp._filter_valid(stock_models.StockSeries.from_raw(raw))
    return [
        (
            dataset_jp._ts_to_date(candle.timestamp, normalize_to_monday=weekly),
            round(float(candle.open)), round(float(candle.high)), round(float(candle.low)),
            round(float(candle.close)), round(float(candle.volume)),
        )
        for candle in series.candles
        if float(candle.volume) > 0
    ]


def collect_jp(weekly: bool = True) -> None:
    """Collect JP OHLCV only; the existing Selenium/Yahoo source is reused."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    import os

    options = webdriver.ChromeOptions()
    if os.getenv("CHROME_HEADLESS", "1") == "1":
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin
    chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
    service = Service(chromedriver_path) if os.path.exists(chromedriver_path) else Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    try:
        stocks = dataset_jp.save_stock_list(static.db_config_jp)
        for stock in stocks:
            for is_weekly, frequency in ((False, stock_lib.FREQUENCY_TYPE_DAY), (True, stock_lib.FREQUENCY_TYPE_WEEK)):
                if is_weekly and not weekly:
                    continue
                raw = dataset_jp.fetch_stock_raw(
                    driver, f"{stock.code}.T", stock_lib.PERIOD_TYPE_MONTH,
                    static.split_collection_months, frequency, 1,
                )
                if raw is not None:
                    _upsert_ohlcv("JP", is_weekly, stock.code, _jp_rows(raw, is_weekly))
    finally:
        driver.quit()


def _round_or_none(value: Any) -> int | None:
    return None if pd.isna(value) else round(float(value))


def _indicator_rows(frame: pd.DataFrame) -> list[tuple[Any, ...]]:
    work = frame.copy()
    close, high, low = work["close"], work["high"], work["low"]
    for window in (5, 20, 50, 60, 120, 240):
        work[f"ma_{window}"] = close.rolling(window=window).mean()
    std60 = close.rolling(window=60).std()
    work["bollinger_upper"] = work["ma_60"] + std60
    work["bollinger_lower"] = work["ma_60"] - std60
    work["bollinger_lower_3"] = work["ma_60"] - (std60 * 3)
    work["conversion"] = (high.rolling(9).max() + low.rolling(9).min()) / 2
    work["base"] = (high.rolling(26).max() + low.rolling(26).min()) / 2
    work["span_a"] = ((work["conversion"] + work["base"]) / 2).shift(26)
    work["span_b"] = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    work["lagging"] = close.shift(-26)
    columns = ("ma_5", "ma_20", "ma_50", "ma_60", "ma_120", "ma_240", "bollinger_upper", "bollinger_lower", "bollinger_lower_3", "conversion", "base", "span_a", "span_b", "lagging")
    return [(row.date, *(_round_or_none(getattr(row, column)) for column in columns)) for row in work.itertuples(index=False)]


def _indicator_rows_for_update(
    rows: list[tuple[Any, ...]], latest_date: date, months: int | None = None
) -> list[tuple[Any, ...]]:
    start_date = _months_before(latest_date, months or static.split_collection_months)
    return [row for row in rows if pd.Timestamp(row[0]).date() >= start_date]


def calculate_indicators(market: str, weekly: bool = True) -> None:
    """Calculate MA, Bollinger (60/1σ and lower 3σ), and Ichimoku from OHLCV."""
    for is_weekly in (False, True) if weekly else (False,):
        tables = _tables(market, is_weekly)
        with psycopg.connect(**static.db_config) as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT DISTINCT code FROM {tables.ohlcv} ORDER BY code")
            codes = [row[0] for row in cursor.fetchall()]
        for code in codes:
            with psycopg.connect(**static.db_config) as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT date, open, high, low, close, volume FROM "
                    f"(SELECT date, open, high, low, close, volume FROM {tables.ohlcv} "
                    f"WHERE code = %s ORDER BY date DESC LIMIT %s) recent ORDER BY date",
                    (code, static.indicator_history_rows),
                )
                frame = pd.DataFrame(cursor.fetchall(), columns=("date", "open", "high", "low", "close", "volume"))
            if frame.empty:
                continue
            latest_date = pd.Timestamp(frame["date"].max()).date()
            payload = [(code, *row) for row in _indicator_rows_for_update(_indicator_rows(frame), latest_date)]
            if not payload:
                continue
            query = f'''INSERT INTO {tables.indicators} (code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg", "120mvavg", "240mvavg", bollinger_upper_60_1, bollinger_lower_60_1, bollinger_lower_60_3, ichimoku_conversion, ichimoku_base, ichimoku_span_a, ichimoku_span_b, ichimoku_lagging, create_date, update_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (code, date) DO UPDATE SET
                "5mvavg" = EXCLUDED."5mvavg", "20mvavg" = EXCLUDED."20mvavg", "50mvavg" = EXCLUDED."50mvavg", "60mvavg" = EXCLUDED."60mvavg", "120mvavg" = EXCLUDED."120mvavg", "240mvavg" = EXCLUDED."240mvavg", bollinger_upper_60_1 = EXCLUDED.bollinger_upper_60_1, bollinger_lower_60_1 = EXCLUDED.bollinger_lower_60_1, bollinger_lower_60_3 = EXCLUDED.bollinger_lower_60_3, ichimoku_conversion = EXCLUDED.ichimoku_conversion, ichimoku_base = EXCLUDED.ichimoku_base, ichimoku_span_a = EXCLUDED.ichimoku_span_a, ichimoku_span_b = EXCLUDED.ichimoku_span_b, ichimoku_lagging = EXCLUDED.ichimoku_lagging, update_date = now()'''
            with psycopg.connect(**static.db_config) as connection, connection.cursor() as cursor:
                cursor.executemany(query, payload)
