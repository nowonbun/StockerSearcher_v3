from __future__ import annotations

import numpy as np

V3_FEATURE_COLS = [
    "close_vs_lowerband60_1", "band_pos_60_1", "drawdown_20d", "ha_ret_1d", "ha_body_ratio",
    "close_vs_cloud_top", "cloud_thickness", "ma20_vs_ma60", "ma20_slope_5", "ma60_slope_10",
]


def _cloud(raw: np.ndarray, high: int, low: int, at: int) -> tuple[float, float] | None:
    # Current cloud is displaced 26 sessions; it needs 52 prior sessions at t-26.
    if at < 77:
        return None
    end = at - 25
    highs, lows = raw[:, high], raw[:, low]
    mid = lambda period: (float(highs[end - period:end].max()) + float(lows[end - period:end].min())) / 2
    span_a = (mid(9) + mid(26)) / 2
    span_b = mid(52)
    return min(span_a, span_b), max(span_a, span_b)


def compute_v3_features(raw: np.ndarray, base_features, raw_columns: list[str]) -> np.ndarray:
    base = base_features(raw)
    idx = {name: i for i, name in enumerate(raw_columns)}
    close = raw[:, idx["Close"]].astype(np.float64)
    ma20 = raw[:, idx["20MvAvg"]].astype(np.float64)
    ma60 = raw[:, idx["60MvAvg"]].astype(np.float64)
    cloud_top = np.full(len(raw), np.nan, dtype=np.float64)
    cloud_width = np.zeros(len(raw), dtype=np.float64)
    for at in range(77, len(raw)):
        cloud = _cloud(raw, idx["High"], idx["Low"], at)
        if cloud:
            bottom, top = cloud
            cloud_top[at], cloud_width[at] = top, (top - bottom) / max(abs(close[at]), 1e-10)
    def slope(values: np.ndarray, periods: int) -> np.ndarray:
        out = np.zeros(len(values), dtype=np.float64)
        out[periods:] = (values[periods:] / np.maximum(np.abs(values[:-periods]), 1e-10)) - 1
        return np.clip(out, -0.2, 0.2)
    extra = np.stack([
        np.nan_to_num(np.clip(close / np.maximum(cloud_top, 1e-10) - 1, -0.5, 0.5), nan=0.0),
        np.clip(cloud_width, 0.0, 0.5),
        np.clip(ma20 / np.maximum(ma60, 1e-10) - 1, -0.5, 0.5),
        slope(ma20, 5), slope(ma60, 10),
    ], axis=1).astype(np.float32)
    return np.concatenate([base, extra], axis=1).astype(np.float32)


def eligible_v3(raw: np.ndarray, end: int, raw_columns: list[str], min_trans_amnt_sum: float = 1_000_000_000) -> bool:
    if end < 77 or end < 4:
        return False
    idx = {name: i for i, name in enumerate(raw_columns)}
    liquid = float(raw[end - 4:end + 1, idx["TransAmnt"]].sum()) >= min_trans_amnt_sum
    ma_pass = raw[end, idx["20MvAvg"]] > raw[end, idx["60MvAvg"]]
    cloud = _cloud(raw, idx["High"], idx["Low"], end)
    cloud_pass = cloud is not None and raw[end, idx["Close"]] > cloud[1]
    return liquid and (ma_pass or cloud_pass)
