from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from predict.common import (  # noqa: E402
    ICHIMOKU_HISTORY_DAYS,
    _ichimoku_cloud_top,
    _passes_selection_filters,
)


RAW_COLUMNS = ["Open", "High", "Low", "Close", "Volume", "TransAmnt", "5MvAvg", "20MvAvg", "50MvAvg", "60MvAvg"]


def args(**overrides: object) -> argparse.Namespace:
    values = {
        "min_trans_amnt_sum": 1_000,
        "liquidity_days": 5,
        "require_ma20_above_ma60": True,
        "require_above_ichimoku_cloud": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def history(close: float = 130.0, ma20: float = 120.0, ma60: float = 110.0, trans_amnt: float = 250.0) -> np.ndarray:
    rows = np.zeros((ICHIMOKU_HISTORY_DAYS, len(RAW_COLUMNS)), dtype=np.float32)
    rows[:, 1] = 110.0  # High
    rows[:, 2] = 90.0   # Low
    rows[:, 3] = close
    rows[:, 5] = trans_amnt
    rows[:, 7] = ma20
    rows[:, 9] = ma60
    return rows


class PredictionFilterTests(unittest.TestCase):
    def test_cloud_top_is_aligned_to_current_date(self) -> None:
        self.assertEqual(_ichimoku_cloud_top(history(), 1, 2), 100.0)

    def test_all_default_filters_accept_qualified_stock(self) -> None:
        self.assertTrue(_passes_selection_filters(history(), RAW_COLUMNS, args()))

    def test_filters_reject_below_cloud_bearish_ma_or_illiquidity(self) -> None:
        self.assertFalse(_passes_selection_filters(history(close=99.0), RAW_COLUMNS, args()))
        self.assertFalse(_passes_selection_filters(history(ma20=100.0, ma60=110.0), RAW_COLUMNS, args()))
        self.assertFalse(_passes_selection_filters(history(trans_amnt=100.0), RAW_COLUMNS, args()))

