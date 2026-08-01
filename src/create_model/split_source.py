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
    return f"stock_data_split_{market.lower()}"


def weekly_split_source(market: str) -> str:
    market = market.upper()
    if market not in {"JP", "KR"}:
        raise ValueError(f"Unsupported market: {market}")
    return f"stock_data_split_week_{market.lower()}"
