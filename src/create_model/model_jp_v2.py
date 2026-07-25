from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd
import torch
import psycopg as postgres
from torch import nn
from torch.utils.data import DataLoader, IterableDataset

import function.static as static
from .model_jp import (
    _RAW_COLS,
    CLOSE_INDEX,
    TRANS_AMNT_INDEX,
    StockTransformer,
    _FilteredTee,
    _build_date_clause,
    _build_ha_series,
    _build_not_null_clause,
    _quote_db_column,
    _pct_change_over,
    get_cutoff_date,
    load_codes,
    load_model_checkpoint,
    train_loop,
)

V2_MODEL_MODE = "v2_upside_probability"
V2_FEATURE_COLS = [
    "close_vs_lowerband60_1",
    "band_pos_60_1",
    "drawdown_20d",
    "ha_ret_1d",
    "ha_body_ratio",
]


def compute_v2_features(raw: np.ndarray) -> np.ndarray:
    idx = {c: i for i, c in enumerate(_RAW_COLS)}
    closes = raw[:, CLOSE_INDEX].astype(np.float64)
    upper = raw[:, idx["UpperBand60_1"]].astype(np.float64)
    lower = raw[:, idx["LowerBand60_1"]].astype(np.float64)
    band_width = np.maximum(upper - lower, 1e-10)

    close_vs_lowerband60_1 = np.clip((closes / (lower + 1e-10)) - 1.0, -0.5, 0.5).astype(np.float32)
    band_pos_60_1 = np.clip((closes - lower) / band_width, -0.5, 1.5).astype(np.float32)

    high20 = pd.Series(closes).rolling(20, min_periods=1).max().values
    drawdown_20d = np.clip((closes / (high20 + 1e-10)) - 1.0, -0.5, 0.1).astype(np.float32)

    ha_close, ha_open, ha_high, ha_low = _build_ha_series(raw)
    ha_ret_1d = np.zeros(len(raw), dtype=np.float32)
    if len(raw) > 1:
        ha_ret_1d[1:] = np.clip(
            (ha_close[1:] - ha_close[:-1]) / (np.abs(ha_close[:-1]) + 1e-10),
            -0.3,
            0.3,
        ).astype(np.float32)
    ha_body_ratio = np.clip(np.abs(ha_close - ha_open) / (np.abs(ha_high - ha_low) + 1e-10), 0.0, 1.0).astype(np.float32)

    return np.stack(
        [
            close_vs_lowerband60_1,
            band_pos_60_1,
            drawdown_20d,
            ha_ret_1d,
            ha_body_ratio,
        ],
        axis=1,
    ).astype(np.float32)


class WindowIterableDatasetV2(IterableDataset):
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
        min_trans_amnt_sum: float | None = None,
        liquidity_days: int = 5,
    ):
        super().__init__()
        self.table = table
        self.codes = codes
        self.start_date = start_date
        self.end_date = end_date
        self.seq_len = seq_len
        self.horizon_days = horizon_days
        self.rise_threshold = rise_threshold
        self.cutoff_date = pd.Timestamp(cutoff_date)
        self.split = split
        self.log_codes = log_codes
        self.log_every = max(1, log_every)
        self.min_trans_amnt_sum = min_trans_amnt_sum
        self.liquidity_days = liquidity_days
        if self.liquidity_days > self.seq_len:
            raise ValueError("liquidity_days cannot exceed seq_len")

    def __iter__(self):
        date_clause, date_params = _build_date_clause(self.start_date, self.end_date)
        not_null = _build_not_null_clause(_RAW_COLS)
        query = (
            f"SELECT date, {', '.join(_quote_db_column(c) for c in _RAW_COLS)} FROM {self.table} "
            f"WHERE code = %s AND {date_clause} AND {not_null} ORDER BY date"
        )

        conn = postgres.connect(**static.db_config_jp)
        try:
            with conn.cursor() as cur:
                for idx_code, code in enumerate(self.codes, start=1):
                    cur.execute(query, (code,) + date_params)
                    rows = cur.fetchall()
                    if not rows:
                        continue
                    if self.log_codes and (idx_code == 1 or idx_code % self.log_every == 0):
                        print(f"[{self.split}] loading code={code} rows={len(rows)} ({idx_code})")

                    dates = np.array([r[0] for r in rows])
                    raw = np.array([r[1:] for r in rows], dtype=np.float32)
                    closes = raw[:, CLOSE_INDEX].astype(np.float64)
                    features = compute_v2_features(raw)
                    max_start = len(features) - (self.seq_len + self.horizon_days) + 1
                    if max_start <= 0:
                        continue

                    for i in range(max_start):
                        end_idx = i + self.seq_len - 1
                        label_date = pd.Timestamp(dates[end_idx])
                        if end_idx <= 0:
                            continue

                        base = closes[end_idx]
                        if base == 0:
                            continue

                        if self.min_trans_amnt_sum is not None:
                            liq_start = end_idx - self.liquidity_days + 1
                            liq_slice = raw[liq_start:end_idx + 1, TRANS_AMNT_INDEX]
                            if float(liq_slice.sum()) < self.min_trans_amnt_sum:
                                continue

                        future_idx = end_idx + self.horizon_days
                        if future_idx >= len(closes):
                            continue

                        # 학습 표본의 정답이 검증 기간에 걸치지 않도록, 실제 정답 시점으로
                        # train/validation 경계를 나눈다. label_date만 사용하면 cutoff 이전의
                        # 입력이라도 미래 가격이 cutoff 이후에 있어 시계열 누수가 발생한다.
                        future_date = pd.Timestamp(dates[future_idx])
                        if self.split == "train" and future_date > self.cutoff_date:
                            continue
                        if self.split == "val" and label_date <= self.cutoff_date:
                            continue

                        future_close = closes[future_idx]
                        label = 1.0 if future_close >= base * (1.0 + self.rise_threshold) else 0.0

                        x = features[i:i + self.seq_len].copy()
                        yield torch.from_numpy(x), torch.tensor(label, dtype=torch.float32)
        finally:
            conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default="STOCK_DATA_JP")
    parser.add_argument("--start-date", default="2000-01-01")
    parser.add_argument("--end-date", default=static.end_date)
    parser.add_argument("--seq-len", type=int, default=60)
    parser.add_argument("--horizon-days", type=int, default=20)
    parser.add_argument("--rise-threshold", type=float, default=0.05)
    parser.add_argument("--min-trans-amnt-sum", type=float, default=1_000_000_000)
    parser.add_argument("--liquidity-days", type=int, default=5)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--clip-grad-norm", type=float, default=0.5)
    parser.add_argument("--d-model", type=int, default=256)
    parser.add_argument("--nhead", type=int, default=8)
    parser.add_argument("--num-encoder-layers", type=int, default=3)
    parser.add_argument("--dim-feedforward", type=int, default=512)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--model-out", default=str(Path(__file__).resolve().parents[1] / "models" / "model_jp_v2.pt"))
    parser.add_argument("--resume", default=None)
    parser.add_argument("--pos-weight", type=float, default=None)
    parser.add_argument("--adaptive-pos-weight", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--pos-weight-step", type=float, default=0.05)
    parser.add_argument("--drop-patience", type=int, default=3)
    parser.add_argument("--use-focal-loss", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--focal-gamma", type=float, default=2.0)
    parser.add_argument("--log-codes", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--log-every", type=int, default=50)
    parser.add_argument("--eval-threshold", type=float, default=0.50)
    parser.add_argument("--auto-threshold", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--threshold-sweep-start", type=float, default=0.35)
    parser.add_argument("--threshold-sweep-end", type=float, default=0.70)
    parser.add_argument("--threshold-sweep-step", type=float, default=0.01)
    parser.add_argument("--pos-rate", type=float, default=None)
    parser.add_argument(
        "--init-pos-rate",
        type=float,
        default=None,
        help="output bias 초기화용 양성 비율; pos_weight는 변경하지 않음",
    )
    parser.add_argument("--pos-weight-max", type=float, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "log",
        f"model_jp_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    tee = _FilteredTee(log_path)
    sys.stdout = tee
    print(f"log={log_path}")
    print("args=" + " ".join(f"{k}={v}" for k, v in vars(args).items()))

    codes = load_codes(args.table, args.start_date, args.end_date)
    if not codes:
        raise RuntimeError("no codes loaded from database")
    print(f"loaded codes={len(codes)}")

    cutoff_date = get_cutoff_date(args.table, args.start_date, args.end_date, args.val_ratio)
    print(f"cutoff_date={cutoff_date.date()}")

    if args.pos_rate is not None and args.pos_weight is not None:
        print(f"[WARN] --pos-rate is set, so --pos-weight={args.pos_weight} will be ignored")
    if args.pos_rate is not None and args.init_pos_rate is not None:
        print("[WARN] --pos-rate is set, so --init-pos-rate will be ignored")

    if args.adaptive_pos_weight and args.pos_rate is None and args.pos_weight is None:
        print("[WARN] adaptive_pos_weight=True but pos_weight is None, so adaptive adjustment is disabled")

    if args.pos_rate is not None:
        if not (0.0 < args.pos_rate < 1.0):
            raise ValueError(f"pos_rate must be in (0, 1), got {args.pos_rate}")
        pos_rate_for_bias = args.pos_rate
        args.pos_weight = (1.0 - args.pos_rate) / args.pos_rate
        print(f"pos_rate={pos_rate_for_bias:.4f} -> pos_weight recalculated as {args.pos_weight:.4f}")
    elif args.pos_weight is not None:
        pos_rate_for_bias = 1.0 / (1.0 + args.pos_weight)
    elif args.init_pos_rate is not None:
        if not (0.0 < args.init_pos_rate < 1.0):
            raise ValueError(f"init_pos_rate must be in (0, 1), got {args.init_pos_rate}")
        pos_rate_for_bias = args.init_pos_rate
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

    train_ds = WindowIterableDatasetV2(
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
        args.min_trans_amnt_sum,
        args.liquidity_days,
    )
    val_ds = WindowIterableDatasetV2(
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
        args.min_trans_amnt_sum,
        args.liquidity_days,
    )

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=False, drop_last=False)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, drop_last=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = StockTransformer(
        input_size=len(V2_FEATURE_COLS),
        d_model=args.d_model,
        nhead=args.nhead,
        num_encoder_layers=args.num_encoder_layers,
        dim_feedforward=args.dim_feedforward,
        dropout=args.dropout,
    ).to(device)

    if args.resume:
        model.load_state_dict(load_model_checkpoint(args.resume, V2_MODEL_MODE, map_location=device))
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
        V2_MODEL_MODE,
    )


if __name__ == "__main__":
    main()
