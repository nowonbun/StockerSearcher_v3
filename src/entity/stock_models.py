from dataclasses import dataclass
from typing import List, Dict, Any, Iterable, Optional


@dataclass(frozen=True)
class StockCandle:
    """단일 캔들(봉) 데이터"""

    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float


class StockSeries:
    """
    여러 캔들을 보관하는 컨테이너.
    내부적으로 List[StockCandle]를 들고 있으며,
    필터링/변환/추가/DF변환 등을 제공.
    """

    def __init__(self, candles: Optional[Iterable[StockCandle]] = None):
        self.candles: List[StockCandle] = list(candles) if candles else []

    def __len__(self) -> int:
        return len(self.candles)

    def __getitem__(self, idx) -> StockCandle:
        return self.candles[idx]

    def append(self, candle: StockCandle) -> None:
        self.candles.append(candle)

    def extend(self, candles: Iterable[StockCandle]) -> None:
        self.candles.extend(candles)

    @classmethod
    def from_raw(cls, data2: Dict[str, List[Any]]) -> "StockSeries":
        """
        기존 dict 형식
        {
            "timestamp": [...],
            "open": [...],
            "high": [...],
            "low": [...],
            "close": [...],
            "volume": [...]
        }
        을 받아 None 이 있는 레코드를 건너뛰고 StockSeries 생성.
        """
        timestamps = data2.get("timestamp", [])
        opens = data2.get("open", [])
        highs = data2.get("high", [])
        lows = data2.get("low", [])
        closes = data2.get("close", [])
        volumes = data2.get("volume", [])

        candles: List[StockCandle] = []
        # zip로 동일 인덱스 묶어서 한 번에 순회 → 깔끔 & 빠름
        for t, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
            if None in (o, h, l, c, v):
                continue
            candles.append(StockCandle(t, o, h, l, c, v))
        return cls(candles)

    def to_raw_dict(self) -> Dict[str, List[Any]]:
        """원래의 dict(list) 구조로 되돌리고 싶을 때"""
        return {
            "timestamp": [c.timestamp for c in self.candles],
            "open": [c.open for c in self.candles],
            "high": [c.high for c in self.candles],
            "low": [c.low for c in self.candles],
            "close": [c.close for c in self.candles],
            "volume": [c.volume for c in self.candles],
        }

    def to_dataframe(self):
        """pandas DataFrame으로 변환(선택사항)"""
        import pandas as pd

        d = self.to_raw_dict()
        return pd.DataFrame(d)
