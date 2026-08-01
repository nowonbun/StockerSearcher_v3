from __future__ import annotations

import numpy as np


WEEK_V3_FEATURE_COLS = [
    "close_vs_lowerband60_1", "band_pos_60_1", "drawdown_20d", "ha_ret_1d", "ha_body_ratio",
    "ma20_vs_ma60", "ma20_slope_5", "ma60_slope_10",
]


def compute_week_v3_features(raw: np.ndarray, base_features, raw_columns: list[str]) -> np.ndarray:
    base = base_features(raw)
    indexes = {name: index for index, name in enumerate(raw_columns)}
    ma20 = raw[:, indexes["20MvAvg"]].astype(np.float64)
    ma60 = raw[:, indexes["60MvAvg"]].astype(np.float64)

    def slope(values: np.ndarray, periods: int) -> np.ndarray:
        result = np.zeros(len(values), dtype=np.float64)
        result[periods:] = (values[periods:] / np.maximum(np.abs(values[:-periods]), 1e-10)) - 1
        return np.clip(result, -0.2, 0.2)

    extra = np.stack([
        np.clip(ma20 / np.maximum(ma60, 1e-10) - 1, -0.5, 0.5),
        slope(ma20, 5),
        slope(ma60, 10),
    ], axis=1).astype(np.float32)
    return np.concatenate([base, extra], axis=1).astype(np.float32)


def eligible_week_v3(raw: np.ndarray, end: int, raw_columns: list[str], min_trans_amnt_sum: float = 1_000_000_000) -> bool:
    if end < 4:
        return False
    indexes = {name: index for index, name in enumerate(raw_columns)}
    liquid = float(raw[end - 4:end + 1, indexes["TransAmnt"]].sum()) >= min_trans_amnt_sum
    return liquid and raw[end, indexes["20MvAvg"]] > raw[end, indexes["60MvAvg"]]
