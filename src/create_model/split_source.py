from __future__ import annotations

from datetime import date, timedelta


def training_cutoff_date(today: date | None = None) -> str:
    current = today or date.today()
    month_start = current.replace(day=1)
    for _ in range(2):
        month_start = (month_start - timedelta(days=1)).replace(day=1)
    return (month_start - timedelta(days=1)).isoformat()


def daily_split_source(market: str) -> str:
    market = market.upper()
    if market not in {"JP", "KR"}:
        raise ValueError(f"Unsupported market: {market}")
    suffix = market.lower()
    return f'''(
        SELECT o.code, o.date, o.open AS "open", o.high AS "high", o.low AS "low",
               o.close AS "close", o.volume AS "volume",
               (o.close::double precision * o.volume) AS "transamnt",
               i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
               i.bollinger_upper_60_1 AS "upperband60_1", i.bollinger_lower_60_1 AS "lowerband60_1"
        FROM stock_ohlcv_{suffix} o
        JOIN stock_indicator_{suffix} i ON i.code = o.code AND i.date = o.date
    ) AS split_source'''
