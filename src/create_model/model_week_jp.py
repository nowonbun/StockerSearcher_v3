from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Tuple

# Allow this file to run directly from ``src/create_model`` by exposing the
# project ``src`` directory, which contains the ``function`` package.
SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader, IterableDataset

import function.static as static
import psycopg as postgres

try:
    from tqdm import tqdm as _tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False


def _prog(it, desc: str, total: int | None):
    """콘솔(stderr)에만 진행률 출력. tqdm 미설치 시 그냥 반환."""
    if not _HAS_TQDM:
        return it
    return _tqdm(it, desc=desc, total=total, file=sys.stderr, leave=False, unit="batch")


# ── 로그 파일 출력 ─────────────────────────────────────────────────────────────

class _FilteredTee:
    """stdout을 콘솔과 로그 파일에 동시 출력. 지정 prefix 행은 파일에서 제외."""
    _SKIP = ("[train] loading", "[val] loading")

    def __init__(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self._file = open(filepath, "w", encoding="utf-8")
        self._orig = sys.__stdout__
        self._buf = ""

    def write(self, text: str) -> None:
        self._orig.write(text)
        self._orig.flush()
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if not any(line.startswith(p) for p in self._SKIP):
                self._file.write(line + "\n")
                self._file.flush()

    def flush(self) -> None:
        self._orig.flush()
        self._file.flush()

    def close(self) -> None:
        if self._buf:
            self._file.write(self._buf)
        self._file.close()


_RAW_COLS = [
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "TransAmnt",
    "5MvAvg",
    "20MvAvg",
    "50MvAvg",
    "60MvAvg",
    "UpperBand60_1",
    "LowerBand60_1",
]

CLOSE_INDEX = _RAW_COLS.index("Close")
TRANS_AMNT_INDEX = _RAW_COLS.index("TransAmnt")
VOLUME_INDEX = _RAW_COLS.index("Volume")

RELATIVE_FEATURE_COLS = [
    "ret_1d",         # 전주 대비 종가 수익률: (이번 주 종가 / 전주 종가) - 1
    "close_vs_ma5",   # 종가의 5주 이동평균 대비 위치: (종가 / 5주선) - 1
    "close_vs_ma20",  # 종가의 20주 이동평균 대비 위치: (종가 / 20주선) - 1
    "close_vs_ma60",  # 종가의 60주 이동평균 대비 위치: (종가 / 60주선) - 1
    "hl_ratio",       # 주간 변동폭 비율: (고가 - 저가) / 종가
    "high_52w_ratio", # 최근 252개 주봉 최고가 대비 종가 위치: 종가 / 252주 최고가
    "ma20_slope_5",   # 20주 이동평균의 최근 5개 주봉 변화율
    "ma60_slope_10",  # 60주 이동평균의 최근 10개 주봉 변화율
]


# ── 파생 피처 계산 ──────────────────────────────────────────────────────────────

def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    rsi = np.full(len(closes), 50.0, dtype=np.float32)
    if len(closes) <= period:
        return rsi
    deltas = np.diff(closes.astype(np.float64))
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(gains[:period].mean())
    avg_loss = float(losses[:period].mean())
    rs = avg_gain / (avg_loss + 1e-10)
    rsi[period] = 100.0 - 100.0 / (1.0 + rs)
    for t in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[t]) / period
        avg_loss = (avg_loss * (period - 1) + losses[t]) / period
        rs = avg_gain / (avg_loss + 1e-10)
        rsi[t + 1] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.empty(len(arr), dtype=np.float32)
    result[0] = float(arr[0])
    k = 2.0 / (period + 1)
    for i in range(1, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1.0 - k)
    return result


def compute_macd(
    closes: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[np.ndarray, np.ndarray]:
    ema_fast = _ema(closes.astype(np.float32), fast)
    ema_slow = _ema(closes.astype(np.float32), slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    return macd_line, signal_line


def _vol_vs_ma(volumes: np.ndarray, window: int) -> np.ndarray:
    """거래량 / rolling_mean(거래량, window) - 1, clipped [-3, 3]."""
    T = len(volumes)
    vol_ma = np.empty(T, dtype=np.float64)
    cumsum = np.cumsum(volumes)
    vol_ma[window - 1:] = (cumsum[window - 1:] - np.concatenate([[0.0], cumsum[:-(window)]])) / window
    vol_ma[:window - 1] = vol_ma[window - 1] if T >= window else (volumes[:window - 1].mean() + 1e-10)
    return np.clip((volumes / (vol_ma + 1e-10)) - 1.0, -3.0, 3.0).astype(np.float32)


def _pct_change_over(arr: np.ndarray, periods: int, clip: tuple[float, float]) -> np.ndarray:
    out = np.zeros(len(arr), dtype=np.float32)
    if len(arr) <= periods:
        return out
    base = arr[:-periods]
    curr = arr[periods:]
    out[periods:] = np.clip((curr / (base + 1e-10)) - 1.0, clip[0], clip[1]).astype(np.float32)
    return out


def compute_relative_features(raw: np.ndarray) -> np.ndarray:
    """
    raw: (T, len(_RAW_COLS)) -> (T, len(RELATIVE_FEATURE_COLS)).
    종목별 가격 단위 차이를 줄이기 위해 종가 위치, 이동평균 기울기,
    변동폭 등 상대값 중심의 주봉 입력 피처를 만든다.
    """
    T = len(raw)
    closes = raw[:, CLOSE_INDEX].astype(np.float64)

    idx = {c: i for i, c in enumerate(_RAW_COLS)}

    ret_1d = np.zeros(T, dtype=np.float32)
    ret_1d[1:] = np.clip(
        (closes[1:] - closes[:-1]) / (np.abs(closes[:-1]) + 1e-10),
        -0.3, 0.3,
    ).astype(np.float32)

    def vs_ma(ma_col: str) -> np.ndarray:
        ma = raw[:, idx[ma_col]].astype(np.float64)
        return np.clip((closes / (ma + 1e-10)) - 1.0, -0.5, 0.5).astype(np.float32)

    close_vs_ma5 = vs_ma("5MvAvg")
    close_vs_ma20 = vs_ma("20MvAvg")
    close_vs_ma60 = vs_ma("60MvAvg")

    highs = raw[:, idx["High"]].astype(np.float64)
    lows = raw[:, idx["Low"]].astype(np.float64)
    hl_ratio = np.clip((highs - lows) / (closes + 1e-10), 0.0, 0.3).astype(np.float32)

    high_52w = pd.Series(highs).rolling(252, min_periods=1).max().values
    high_52w_ratio = np.clip(closes / (high_52w + 1e-10), 0.0, 1.0).astype(np.float32)

    ma20 = raw[:, idx["20MvAvg"]].astype(np.float64)
    ma60 = raw[:, idx["60MvAvg"]].astype(np.float64)
    ma20_slope_5 = _pct_change_over(ma20, 5, (-0.2, 0.2))
    ma60_slope_10 = _pct_change_over(ma60, 10, (-0.2, 0.2))

    out = np.stack(
        [
            ret_1d,
            close_vs_ma5,
            close_vs_ma20,
            close_vs_ma60,
            hl_ratio,
            high_52w_ratio,
            ma20_slope_5,
            ma60_slope_10,
        ],
        axis=1,
    )
    return out.astype(np.float32)


def extract_trend_filter_metrics(features: np.ndarray) -> dict[str, float]:
    latest = features[-1]
    return {
        "close_vs_ma20": float(latest[RELATIVE_FEATURE_COLS.index("close_vs_ma20")]),
        "close_vs_ma60": float(latest[RELATIVE_FEATURE_COLS.index("close_vs_ma60")]),
        "high_52w_ratio": float(latest[RELATIVE_FEATURE_COLS.index("high_52w_ratio")]),
        "ma20_slope_5": float(latest[RELATIVE_FEATURE_COLS.index("ma20_slope_5")]),
        "ma60_slope_10": float(latest[RELATIVE_FEATURE_COLS.index("ma60_slope_10")]),
    }


# ── Focal Loss ─────────────────────────────────────────────────────────────────

class FocalLoss(nn.Module):
    def __init__(
        self,
        alpha: float = 1.0,
        gamma: float = 2.0,
        pos_weight: torch.Tensor | None = None,
    ):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.pos_weight = pos_weight

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        bce = F.binary_cross_entropy_with_logits(
            logits, targets, pos_weight=self.pos_weight, reduction="none"
        )
        probs = torch.sigmoid(logits)
        p_t = probs * targets + (1.0 - probs) * (1.0 - targets)
        weight = self.alpha * (1.0 - p_t) ** self.gamma
        return (weight * bce).mean()


# ── 모델 ───────────────────────────────────────────────────────────────────────

class StockTransformer(nn.Module):
    """
    Transformer Encoder 기반 주가 예측 모델.
    GRU의 마지막 hidden state 대신 전체 시퀀스에 self-attention을 적용해
    임의 시점 간의 장거리 의존성을 직접 포착한다.

    구조:
        input_proj  : Linear(input_size → d_model)
        pos_emb     : Learnable positional embedding (최대 2000 스텝)
        encoder     : TransformerEncoder (Pre-LN, batch_first=True)
        pool        : mean pooling over time dimension
        head        : Linear → LayerNorm → ReLU → Dropout → Linear(1)
    """

    def __init__(
        self,
        input_size: int,
        d_model: int,
        nhead: int,
        num_encoder_layers: int,
        dim_feedforward: int,
        dropout: float,
    ):
        super().__init__()
        self.input_proj = nn.Linear(input_size, d_model)
        self.pos_emb = nn.Embedding(2000, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            batch_first=True,
            norm_first=True,   # Pre-LayerNorm → 학습 안정성 향상
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_encoder_layers)
        self.head = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.LayerNorm(d_model // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, input_size)
        B, T, _ = x.shape
        pos = torch.arange(T, device=x.device).unsqueeze(0)  # (1, T)
        h = self.input_proj(x) + self.pos_emb(pos)           # (B, T, d_model)
        h = self.encoder(h)                                    # (B, T, d_model)
        h = h.mean(dim=1)                                      # mean pooling → (B, d_model)
        return self.head(h).squeeze(-1)                        # (B,)


# ── DB 헬퍼 ────────────────────────────────────────────────────────────────────

def _build_date_clause(
    start_date: str | None, end_date: str | None
) -> Tuple[str, tuple]:
    if start_date and end_date:
        return "date BETWEEN %s AND %s", (start_date, end_date)
    return "1=1", ()


def _quote_db_column(column: str) -> str:
    """Return a quoted PostgreSQL column name for a fixed model feature."""
    return f'"{column.lower()}"'


def _build_not_null_clause(cols: Iterable[str]) -> str:
    return " AND ".join([f"{_quote_db_column(c)} IS NOT NULL" for c in cols if c != "LowerBand60_3"])


def load_codes(
    table: str,
    start_date: str | None,
    end_date: str | None,
) -> List[str]:
    date_clause, params = _build_date_clause(start_date, end_date)
    query = f"SELECT DISTINCT code FROM {table} WHERE {date_clause} ORDER BY code"
    conn = postgres.connect(**static.db_config_jp)
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_cutoff_date(
    table: str,
    start_date: str | None,
    end_date: str | None,
    val_ratio: float,
) -> pd.Timestamp:
    date_clause, params = _build_date_clause(start_date, end_date)
    query = f"SELECT DISTINCT date FROM {table} WHERE {date_clause} ORDER BY date"
    conn = postgres.connect(**static.db_config_jp)
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            dates = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    if not dates:
        raise ValueError("no dates available to split")
    cut_idx = int(len(dates) * (1.0 - val_ratio))
    return pd.Timestamp(dates[max(0, min(len(dates) - 1, cut_idx))])


# ── 데이터셋 ───────────────────────────────────────────────────────────────────

class WindowIterableDataset(IterableDataset):
    def __init__(
        self,
        table: str,
        codes: List[str],
        start_date: str | None,
        end_date: str | None,
        seq_len: int,
        horizon_days: int,
        rise_threshold: float,
        cutoff_date: pd.Timestamp,
        split: str,
        log_codes: bool,
        log_every: int,
        max_drawdown: float = 0.10,
        min_trans_amnt_sum: float | None = None,
        liquidity_days: int = 5,
        trend_label_min_high_52w_ratio: float = 0.85,
        trend_label_min_close_vs_ma20: float = -0.02,
        trend_label_min_ma20_slope: float = -0.01,
        trend_label_min_ma60_slope: float = -0.01,
        trend_label_filter: bool = False,
    ):
        super().__init__()
        self.seq_len = seq_len
        self.horizon_days = horizon_days
        self.rise_threshold = rise_threshold
        self.cutoff_date = pd.Timestamp(cutoff_date)
        self.split = split
        self.table = table
        self.codes = codes
        self.start_date = start_date
        self.end_date = end_date
        self.log_codes = log_codes
        self.log_every = max(1, log_every)
        self.max_drawdown = max_drawdown
        self.min_trans_amnt_sum = min_trans_amnt_sum
        self.liquidity_days = liquidity_days
        self.trend_label_min_high_52w_ratio = trend_label_min_high_52w_ratio
        self.trend_label_min_close_vs_ma20 = trend_label_min_close_vs_ma20
        self.trend_label_min_ma20_slope = trend_label_min_ma20_slope
        self.trend_label_min_ma60_slope = trend_label_min_ma60_slope
        self.trend_label_filter = trend_label_filter
        if self.liquidity_days > self.seq_len:
            raise ValueError("liquidity_days cannot exceed seq_len")

    def __iter__(self):
        date_clause, date_params = _build_date_clause(self.start_date, self.end_date)
        not_null = _build_not_null_clause(_RAW_COLS)
        where = f"code = %s AND {date_clause} AND {not_null}"
        query = (
            f"SELECT date, {', '.join(_quote_db_column(c) for c in _RAW_COLS)} FROM {self.table} "
            f"WHERE {where} ORDER BY date"
        )

        conn = postgres.connect(**static.db_config_jp)
        try:
            with conn.cursor() as cur:
                for idx, code in enumerate(self.codes, start=1):
                    params = (code,) + date_params
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    if not rows:
                        continue
                    if self.log_codes and (idx == 1 or idx % self.log_every == 0):
                        print(f"[{self.split}] loading code={code} rows={len(rows)} ({idx})")

                    dates = np.array([r[0] for r in rows])
                    raw = np.array([r[1:] for r in rows], dtype=np.float32)
                    closes = raw[:, CLOSE_INDEX]

                    features = compute_relative_features(raw)

                    max_start = len(features) - (self.seq_len + self.horizon_days) + 1
                    if max_start <= 0:
                        continue
                    for i in range(max_start):
                        end_idx = i + self.seq_len - 1
                        label_date = pd.Timestamp(dates[end_idx])
                        if self.split == "train" and label_date > self.cutoff_date:
                            continue
                        if self.split == "val" and label_date <= self.cutoff_date:
                            continue

                        base = closes[end_idx]
                        if base == 0:
                            continue
                        if self.min_trans_amnt_sum is not None:
                            liq_start = end_idx - self.liquidity_days + 1
                            liq_slice = raw[liq_start : end_idx + 1, TRANS_AMNT_INDEX]
                            if float(liq_slice.sum()) < self.min_trans_amnt_sum:
                                continue
                        future_idx = end_idx + self.horizon_days
                        if future_idx >= len(closes):
                            continue
                        target = base * (1.0 + self.rise_threshold)
                        window_closes = closes[end_idx + 1 : future_idx + 1]
                        if window_closes.size == 0:
                            continue
                        future_close = float(closes[future_idx])
                        max_pullback = float(window_closes.min() / (base + 1e-10) - 1.0)
                        base_label = future_close >= target and max_pullback >= -self.max_drawdown
                        if self.trend_label_filter:
                            future_high_52w_ratio = float(features[future_idx, RELATIVE_FEATURE_COLS.index("high_52w_ratio")])
                            future_close_vs_ma20 = float(features[future_idx, RELATIVE_FEATURE_COLS.index("close_vs_ma20")])
                            future_ma20_slope = float(features[future_idx, RELATIVE_FEATURE_COLS.index("ma20_slope_5")])
                            future_ma60_slope = float(features[future_idx, RELATIVE_FEATURE_COLS.index("ma60_slope_10")])
                            trend_filter_ok = (
                                future_high_52w_ratio >= self.trend_label_min_high_52w_ratio
                                and future_close_vs_ma20 >= self.trend_label_min_close_vs_ma20
                                and future_ma20_slope >= self.trend_label_min_ma20_slope
                                and future_ma60_slope >= self.trend_label_min_ma60_slope
                            )
                        else:
                            trend_filter_ok = True
                        label = 1.0 if base_label and trend_filter_ok else 0.0

                        x = features[i : i + self.seq_len].copy()  # (seq_len, len(RELATIVE_FEATURE_COLS))
                        yield torch.from_numpy(x), torch.tensor(label, dtype=torch.float32)
        finally:
            conn.close()


# ── 학습 루프 ──────────────────────────────────────────────────────────────────

def train_loop(
    model: nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device: torch.device,
    epochs: int,
    lr: float,
    model_out: str,
    pos_weight: float | None,
    eval_threshold: float,
    adaptive_pos_weight: bool,
    pos_weight_step: float,
    drop_patience: int,
    clip_grad_norm: float,
    use_focal_loss: bool,
    focal_gamma: float,
    auto_threshold: bool,
    threshold_sweep_start: float,
    threshold_sweep_end: float,
    threshold_sweep_step: float,
    pos_weight_max: float | None,
) -> None:
    def compute_metrics_from_probs(
        probs: np.ndarray,
        targets: np.ndarray,
        threshold: float,
    ) -> tuple[float, float, float, float, int, int, int]:
        preds = (probs >= threshold).astype(np.float32)
        correct = int((preds == targets).sum())
        tp = int(((preds == 1.0) & (targets == 1.0)).sum())
        fp = int(((preds == 1.0) & (targets == 0.0)).sum())
        fn = int(((preds == 0.0) & (targets == 1.0)).sum())
        total = len(targets)
        acc = correct / total if total else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2.0 * prec * rec / (prec + rec + 1e-8) if (prec + rec) > 0 else 0.0
        return acc, prec, rec, f1, tp, fp, fn

    def find_best_threshold(
        probs: np.ndarray,
        targets: np.ndarray,
    ) -> tuple[float, float, float, float, float, int, int, int]:
        if threshold_sweep_step <= 0:
            raise ValueError(f'threshold_sweep_step must be > 0, got {threshold_sweep_step}')
        if threshold_sweep_end < threshold_sweep_start:
            raise ValueError(
                f'threshold_sweep_end must be >= threshold_sweep_start, got '
                f'{threshold_sweep_start} > {threshold_sweep_end}'
            )
        num_thresholds = int(round((threshold_sweep_end - threshold_sweep_start) / threshold_sweep_step)) + 1
        thresholds = np.linspace(
            threshold_sweep_start,
            threshold_sweep_end,
            num_thresholds,
            dtype=np.float32,
        )
        if thresholds.size == 0:
            raise ValueError('threshold sweep range is empty')

        best_thr = float(thresholds[0])
        best_acc = best_prec = best_rec = 0.0
        best_f1_local = -1.0
        best_tp = best_fp = best_fn = 0

        for thr in thresholds:
            acc, prec, rec, f1, tp, fp, fn = compute_metrics_from_probs(
                probs, targets, float(thr)
            )
            if (
                f1 > best_f1_local
                or (
                    abs(f1 - best_f1_local) <= 1e-8
                    and (prec > best_prec or (abs(prec - best_prec) <= 1e-8 and float(thr) > best_thr))
                )
            ):
                best_thr = float(thr)
                best_acc = acc
                best_prec = prec
                best_rec = rec
                best_f1_local = f1
                best_tp = tp
                best_fp = fp
                best_fn = fn

        return best_thr, best_acc, best_prec, best_rec, best_f1_local, best_tp, best_fp, best_fn

    def build_criterion(weight: float | None) -> nn.Module:
        pw = torch.tensor([weight], device=device) if weight is not None else None
        if use_focal_loss:
            return FocalLoss(gamma=focal_gamma, pos_weight=pw)
        return nn.BCEWithLogitsLoss(pos_weight=pw) if pw is not None else nn.BCEWithLogitsLoss()

    current_weight = pos_weight
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_f1 = -1.0
    prev_prec: float | None = None
    prev_rec: float | None = None
    metric_eps = 1e-6
    drop_streak = 0
    _prev_train_batches: int | None = None
    _prev_val_batches: int | None = None

    for epoch in range(1, epochs + 1):
        criterion = build_criterion(current_weight)

        model.train()
        train_loss = 0.0
        train_count = 0
        train_pos_sum = 0.0
        _train_batches = 0
        for x, y in _prog(train_loader, f"ep{epoch}/{epochs} train", _prev_train_batches):
            x, y = x.to(device), y.to(device)
            pred = model(x)
            loss = criterion(pred, y)
            optimizer.zero_grad()
            loss.backward()
            if clip_grad_norm > 0:
                nn.utils.clip_grad_norm_(model.parameters(), clip_grad_norm)
            optimizer.step()
            train_loss += loss.item() * x.size(0)
            train_count += x.size(0)
            train_pos_sum += y.sum().item()
            _train_batches += 1
        _prev_train_batches = _train_batches
        train_loss = train_loss / train_count if train_count else 0.0
        train_pos_rate = train_pos_sum / train_count if train_count else 0.0
        scheduler.step()
        print(f'  -> epoch={epoch} train_samples={train_count}')

        model.eval()
        val_loss = 0.0
        total = 0
        val_pos_sum = 0.0
        val_probs: list[np.ndarray] = []
        val_targets: list[np.ndarray] = []
        _val_batches = 0
        with torch.no_grad():
            for x, y in _prog(val_loader, f"ep{epoch}/{epochs} val  ", _prev_val_batches):
                x, y = x.to(device), y.to(device)
                pred = model(x)
                loss = criterion(pred, y)
                val_loss += loss.item() * x.size(0)
                probs = torch.sigmoid(pred)
                val_probs.append(probs.detach().cpu().numpy())
                val_targets.append(y.detach().cpu().numpy())
                val_pos_sum += y.sum().item()
                total += y.size(0)
                _val_batches += 1
        _prev_val_batches = _val_batches
        val_loss = val_loss / total if total else 0.0
        val_pos_rate = val_pos_sum / total if total else 0.0
        print(f'  -> epoch={epoch} val_samples={total}')

        if total:
            probs_np = np.concatenate(val_probs).astype(np.float32)
            targets_np = np.concatenate(val_targets).astype(np.float32)
            if auto_threshold:
                eval_thr, acc, prec, rec, f1, tp, fp, fn = find_best_threshold(
                    probs_np, targets_np
                )
            else:
                eval_thr = eval_threshold
                acc, prec, rec, f1, tp, fp, fn = compute_metrics_from_probs(
                    probs_np, targets_np, eval_thr
                )
            print(
                "  -> prob stats "
                f"min={probs_np.min():.4f} "
                f"p25={np.quantile(probs_np, 0.25):.4f} "
                f"p50={np.quantile(probs_np, 0.50):.4f} "
                f"p75={np.quantile(probs_np, 0.75):.4f} "
                f"max={probs_np.max():.4f} "
                f"mean={probs_np.mean():.4f}"
            )
        else:
            eval_thr = eval_threshold
            acc = prec = rec = f1 = 0.0
            tp = fp = fn = 0

        current_lr = scheduler.get_last_lr()[0]

        print(
            f'epoch={epoch} train_loss={train_loss:.6f} val_loss={val_loss:.6f} '
            f'train_pos_rate={train_pos_rate:.4f} val_pos_rate={val_pos_rate:.4f} '
            f'acc={acc:.4f} prec={prec:.4f} rec={rec:.4f} f1={f1:.4f} '
            f'thr={eval_thr:.2f} lr={current_lr:.6f}'
            + (f' pw={current_weight:.4f}' if current_weight is not None else '')
        )
        print(f'  -> confusion tp={tp} fp={fp} fn={fn} tn={total - tp - fp - fn}')

        if f1 > best_f1:
            best_f1 = f1
            torch.save(model.state_dict(), model_out)
            print(f'  -> saved best model (epoch={epoch}, f1={f1:.4f}, best_f1={best_f1:.4f})')
        else:
            print(f'  -> not saved (epoch={epoch}, f1={f1:.4f}, best_f1={best_f1:.4f})')

        drop_any = (
            prev_prec is not None
            and prev_rec is not None
            and (prec < prev_prec - metric_eps or rec < prev_rec - metric_eps)
        )
        if adaptive_pos_weight and drop_any and current_weight is not None:
            drop_streak += 1
            if prec < prev_prec - metric_eps:
                current_weight *= (1.0 - pos_weight_step)
            else:
                current_weight *= (1.0 + pos_weight_step)
            current_weight = max(1.0, current_weight)
            if pos_weight_max is not None:
                current_weight = min(current_weight, pos_weight_max)
            print(f'adaptive pos_weight -> {current_weight:.4f}')
            prev_prec = prec
            prev_rec = rec
            if drop_streak >= max(1, drop_patience):
                print('drop streak reached: early stop')
                break
            continue

        drop_streak = 0
        prev_prec = prec
        prev_rec = rec


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    # 데이터 범위
    parser.add_argument("--table", default="STOCK_DATA_WEEK_JP", help="DB table name")
    parser.add_argument("--start-date", default="2000-01-01", help="데이터 시작일 (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=static.end_date, help="데이터 종료일 (YYYY-MM-DD)")
    # 윈도우/라벨 정의
    parser.add_argument("--seq-len", type=int, default=120, help="시퀀스 길이(일)")
    parser.add_argument("--horizon-days", type=int, default=20, help="label horizon in weekly rows")
    parser.add_argument("--rise-threshold", type=float, default=0.08, help="목표 상승률 (예: 0.10 = +10%%)")
    parser.add_argument("--max-drawdown", type=float, default=0.10, help="기간 내 허용 최대 낙폭")
    parser.add_argument("--min-trans-amnt-sum", type=float, default=1_000_000_000, help="유동성 기간 내 TransAmnt 합 최소값")
    parser.add_argument("--liquidity-days", type=int, default=5, help="TransAmnt 합 계산 기간(일)")
    parser.add_argument("--trend-label-min-high-52w-ratio", type=float, default=0.85, help="추세 라벨에서 요구하는 52주 고점 근접 비율")
    parser.add_argument("--trend-label-min-close-vs-ma20", type=float, default=-0.02, help="추세 라벨에서 허용하는 미래 close_vs_ma20 최저값")
    parser.add_argument("--trend-label-min-ma20-slope", type=float, default=-0.01, help="추세 라벨에서 허용하는 미래 MA20 기울기 최저값")
    parser.add_argument("--trend-label-min-ma60-slope", type=float, default=-0.01, help="추세 라벨에서 허용하는 미래 MA60 기울기 최저값")
    parser.add_argument("--trend-label-filter", action=argparse.BooleanOptionalAction, default=False, help="미래 추세 조건으로 양성 라벨을 추가 선별할지 여부")
    # 학습/검증 분리
    parser.add_argument("--val-ratio", type=float, default=0.2, help="날짜 기준 검증 비율")
    # 학습 루프
    parser.add_argument("--batch-size", type=int, default=256, help="batch size")
    parser.add_argument("--epochs", type=int, default=30, help="학습 에폭 수")
    parser.add_argument("--lr", type=float, default=1e-3, help="학습률")
    parser.add_argument("--clip-grad-norm", type=float, default=0.5, help="gradient clipping max norm (0=비활성)")
    # 모델 구조 (Transformer)
    parser.add_argument("--d-model", type=int, default=256, help="Transformer d_model (임베딩 차원)")
    parser.add_argument("--nhead", type=int, default=8, help="Multi-head attention 헤드 수 (d_model의 약수여야 함)")
    parser.add_argument("--num-encoder-layers", type=int, default=3, help="Transformer Encoder 레이어 수")
    parser.add_argument("--dim-feedforward", type=int, default=512, help="Transformer FFN 내부 차원")
    parser.add_argument("--dropout", type=float, default=0.2, help="드롭아웃")
    # 체크포인트 및 클래스 불균형
    # checkpoint
    parser.add_argument("--model-out", default=str(Path(__file__).resolve().parents[1] / "models" / "model_week_jp.pt"), help="model output path")
    parser.add_argument("--resume", default=None, help="재개 모델 경로")
    parser.add_argument("--pos-weight", type=float, default=None, help="BCE pos_weight")
    parser.add_argument("--adaptive-pos-weight", action=argparse.BooleanOptionalAction, default=False, help="prec/rec 하락 시 pos_weight 적응 조정")
    parser.add_argument("--pos-weight-step", type=float, default=0.05, help="pos_weight 조정 비율 (예: 0.05 = 5%%)")
    parser.add_argument("--drop-patience", type=int, default=3, help="연속 하락 횟수로 조기 종료")
    # Focal Loss
    parser.add_argument("--use-focal-loss", action=argparse.BooleanOptionalAction, default=True, help="BCEWithLogitsLoss 대신 Focal Loss 사용")
    parser.add_argument("--focal-gamma", type=float, default=2.0, help="Focal Loss gamma 파라미터")
    # 진행 로그 및 평가
    parser.add_argument("--log-codes", action=argparse.BooleanOptionalAction, default=False, help="코드별 로딩 로그 출력")
    parser.add_argument("--log-every", type=int, default=50, help="코드 로그 출력 간격")
    parser.add_argument("--eval-threshold", type=float, default=0.45, help="평가용 확률 임계값")
    parser.add_argument("--auto-threshold", action=argparse.BooleanOptionalAction, default=True, help="auto-select best validation threshold by F1 sweep")
    parser.add_argument("--threshold-sweep-start", type=float, default=0.70, help="threshold sweep start")
    parser.add_argument("--threshold-sweep-end", type=float, default=0.95, help="threshold sweep end")
    parser.add_argument("--threshold-sweep-step", type=float, default=0.01, help="threshold sweep step")
    parser.add_argument("--pos-rate", type=float, default=0.06,
                        help="actual positive rate; if set, recompute pos_weight and init output bias")
    parser.add_argument("--pos-weight-max", type=float, default=20, help="cap pos_weight (<=0 disables)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "log",
        f"model_week_jp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    _tee = _FilteredTee(_log_path)
    sys.stdout = _tee
    print(f"log={_log_path}")
    print("args=" + " ".join(f"{k}={v}" for k, v in vars(args).items()))

    codes = load_codes(args.table, args.start_date, args.end_date)
    if not codes:
        raise RuntimeError("no codes loaded from database")

    print(f"loaded codes={len(codes)}")
    cutoff_date = get_cutoff_date(args.table, args.start_date, args.end_date, args.val_ratio)
    print(f"cutoff_date={cutoff_date.date()}")

    if args.pos_rate is not None and args.pos_weight is not None:
        print(f"[WARN] --pos-rate is set, so --pos-weight={args.pos_weight} will be ignored")

    if args.adaptive_pos_weight and args.pos_rate is None and args.pos_weight is None:
        print("[WARN] adaptive_pos_weight=True but pos_weight is None, so adaptive adjustment is disabled")

    if args.pos_rate is not None:
        if not (0.0 < args.pos_rate < 1.0):
            raise ValueError(f"pos_rate must be in (0, 1), got {args.pos_rate}")
        pos_rate_for_bias = args.pos_rate
        args.pos_weight = (1.0 - args.pos_rate) / args.pos_rate
        print(f"pos_rate={pos_rate_for_bias:.4f} → pos_weight 재산출={args.pos_weight:.4f}")
    elif args.pos_weight is not None:
        pos_rate_for_bias = 1.0 / (1.0 + args.pos_weight)
    else:
        pos_rate_for_bias = None

    if args.pos_weight_max is not None and args.pos_weight_max <= 0:
        args.pos_weight_max = None

    if args.pos_weight_max is not None:
        if args.pos_weight_max < 1.0:
            raise ValueError(f"pos_weight_max must be >= 1.0, got {args.pos_weight_max}")
        if args.pos_weight is not None and args.pos_weight > args.pos_weight_max:
            print(f"pos_weight capped: {args.pos_weight:.4f} -> {args.pos_weight_max:.4f}")
            args.pos_weight = args.pos_weight_max

    if args.d_model % args.nhead != 0:
        raise ValueError(f"d_model({args.d_model}) must be divisible by nhead({args.nhead})")

    train_ds = WindowIterableDataset(
        args.table,
        codes,
        args.start_date,
        args.end_date,
        args.seq_len,
        args.horizon_days,
        args.rise_threshold,
        cutoff_date,
        "train",
        args.log_codes,
        args.log_every,
        args.max_drawdown,
        args.min_trans_amnt_sum,
        args.liquidity_days,
        args.trend_label_min_high_52w_ratio,
        args.trend_label_min_close_vs_ma20,
        args.trend_label_min_ma20_slope,
        args.trend_label_min_ma60_slope,
        args.trend_label_filter,
    )
    val_ds = WindowIterableDataset(
        args.table,
        codes,
        args.start_date,
        args.end_date,
        args.seq_len,
        args.horizon_days,
        args.rise_threshold,
        cutoff_date,
        "val",
        args.log_codes,
        args.log_every,
        args.max_drawdown,
        args.min_trans_amnt_sum,
        args.liquidity_days,
        args.trend_label_min_high_52w_ratio,
        args.trend_label_min_close_vs_ma20,
        args.trend_label_min_ma20_slope,
        args.trend_label_min_ma60_slope,
        args.trend_label_filter,
    )

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=False, drop_last=False)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, drop_last=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = StockTransformer(
        input_size=len(RELATIVE_FEATURE_COLS),
        d_model=args.d_model,
        nhead=args.nhead,
        num_encoder_layers=args.num_encoder_layers,
        dim_feedforward=args.dim_feedforward,
        dropout=args.dropout,
    ).to(device)

    if args.resume:
        model.load_state_dict(torch.load(args.resume, map_location=device, weights_only=True))
    elif pos_rate_for_bias is not None:
        bias_init = math.log(pos_rate_for_bias / (1.0 - pos_rate_for_bias))
        with torch.no_grad():
            model.head[-1].bias.fill_(bias_init)
        print(f"output bias initialized to {bias_init:.4f} (pos_rate={pos_rate_for_bias:.4f})")

    train_loop(
        model,
        train_loader,
        val_loader,
        device,
        args.epochs,
        args.lr,
        args.model_out,
        args.pos_weight,
        args.eval_threshold,
        args.adaptive_pos_weight,
        args.pos_weight_step,
        args.drop_patience,
        args.clip_grad_norm,
        args.use_focal_loss,
        args.focal_gamma,
        args.auto_threshold,
        args.threshold_sweep_start,
        args.threshold_sweep_end,
        args.threshold_sweep_step,
        args.pos_weight_max,
    )


if __name__ == "__main__":
    main()
