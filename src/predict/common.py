"""Shared CPU inference runner for the four PostgreSQL stock prediction jobs."""

from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import torch

import function.static as static
import psycopg as postgres


@dataclass(frozen=True)
class PredictionSpec:
    market: str
    table: str
    prediction_table: str
    base_module: str
    v2_module: str
    model_file: str
    seq_len: int
    horizon_days: int
    rise_threshold: float
    top_k: int
    default_min_prob: float | None = None
    default_require_ma20_above_ma60: bool = False
    default_require_above_ichimoku_cloud: bool = False
    model_mode: str | None = None


ICHIMOKU_CONVERSION_PERIOD = 9
ICHIMOKU_BASE_PERIOD = 26
ICHIMOKU_SPAN_B_PERIOD = 52
ICHIMOKU_DISPLACEMENT = 26
ICHIMOKU_HISTORY_DAYS = ICHIMOKU_SPAN_B_PERIOD + ICHIMOKU_DISPLACEMENT


def _not_null_clause(columns: Iterable[str], excluded: set[str]) -> str:
    return " AND ".join(f'"{column.lower()}" IS NOT NULL' for column in columns if column not in excluded)


def parse_args(spec: PredictionSpec) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Run {spec.market} V2 stock prediction.")
    parser.add_argument("--table", default=spec.table)
    parser.add_argument("--start-date", default=static.start_date)
    parser.add_argument("--end-date", default=static.end_date)
    parser.add_argument("--seq-len", type=int, default=spec.seq_len)
    parser.add_argument("--horizon-days", type=int, default=spec.horizon_days)
    parser.add_argument("--rise-threshold", type=float, default=spec.rise_threshold)
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--model", default=str(Path(__file__).resolve().parents[1] / "models" / spec.model_file))
    parser.add_argument("--d-model", type=int, default=256)
    parser.add_argument("--nhead", type=int, default=8)
    parser.add_argument("--num-encoder-layers", type=int, default=3)
    parser.add_argument("--dim-feedforward", type=int, default=512)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--top-k", type=int, default=spec.top_k)
    parser.add_argument("--min-prob", type=float, default=spec.default_min_prob)
    parser.add_argument("--min-trans-amnt-sum", type=float, default=1_000_000_000)
    parser.add_argument("--liquidity-days", type=int, default=5)
    parser.add_argument("--require-ma20-above-ma60", action=argparse.BooleanOptionalAction, default=spec.default_require_ma20_above_ma60)
    parser.add_argument("--require-above-ichimoku-cloud", action=argparse.BooleanOptionalAction, default=spec.default_require_above_ichimoku_cloud)
    parser.add_argument("--log-every", type=int, default=200)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--code", default=None)
    return parser.parse_args()


def _fetch_raw_history(connection: postgres.Connection, table: str, code: str, history_days: int, cutoff: str | None, raw_columns: list[str], excluded_columns: set[str]) -> np.ndarray | None:
    where = f"code = %s AND {_not_null_clause(raw_columns, excluded_columns)}"
    params: tuple[object, ...] = (code, history_days)
    if cutoff:
        where = f"code = %s AND date <= %s AND {_not_null_clause(raw_columns, excluded_columns)}"
        params = (code, cutoff, history_days)
    selected_columns = ", ".join(f'"{column.lower()}"' for column in raw_columns)
    query = f"SELECT {selected_columns} FROM {table} WHERE {where} ORDER BY date DESC LIMIT %s"
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    return None if len(rows) < history_days else np.array(rows[::-1], dtype=np.float32)


def _ichimoku_cloud_top(raw: np.ndarray, high_index: int, low_index: int) -> float | None:
    """Return the cloud top aligned to the last row's date.

    Senkou spans are displaced 26 sessions forward, so today's cloud uses
    conversion/base/span-B values calculated 26 sessions ago.
    """
    if len(raw) < ICHIMOKU_HISTORY_DAYS:
        return None
    source_end = len(raw) - ICHIMOKU_DISPLACEMENT
    highs = raw[:, high_index].astype(np.float64)
    lows = raw[:, low_index].astype(np.float64)

    def midpoint(period: int) -> float:
        window_high = highs[source_end - period:source_end]
        window_low = lows[source_end - period:source_end]
        return float((window_high.max() + window_low.min()) / 2.0)

    conversion = midpoint(ICHIMOKU_CONVERSION_PERIOD)
    base = midpoint(ICHIMOKU_BASE_PERIOD)
    span_a = (conversion + base) / 2.0
    span_b = midpoint(ICHIMOKU_SPAN_B_PERIOD)
    return max(span_a, span_b)


def _passes_selection_filters(raw: np.ndarray, raw_columns: list[str], args: argparse.Namespace) -> bool:
    indexes = {column: index for index, column in enumerate(raw_columns)}
    if args.min_trans_amnt_sum is not None:
        if args.liquidity_days <= 0 or args.liquidity_days > len(raw):
            return False
        recent_trans_amnt = raw[-args.liquidity_days:, indexes["TransAmnt"]].sum()
        if recent_trans_amnt < args.min_trans_amnt_sum:
            return False
    trend_passes: list[bool] = []
    if args.require_ma20_above_ma60:
        trend_passes.append(raw[-1, indexes["20MvAvg"]] > raw[-1, indexes["60MvAvg"]])
    if args.require_above_ichimoku_cloud:
        cloud_top = _ichimoku_cloud_top(raw, indexes["High"], indexes["Low"])
        trend_passes.append(cloud_top is not None and raw[-1, indexes["Close"]] > cloud_top)
    if trend_passes and not any(trend_passes):
        return False
    return True


def _save_predictions(spec: PredictionSpec, args: argparse.Namespace, rows: list[tuple[str, float]], cutoff: str) -> None:
    connection = postgres.connect(**(static.db_config_jp if spec.market == "JP" else static.db_config_kr))
    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                f"""
                INSERT INTO {spec.prediction_table}
                    (data_cutoff, code, probability, run_name, seq_len, horizon_days, rise_threshold, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (data_cutoff, code, run_name) DO UPDATE SET
                    probability = EXCLUDED.probability,
                    seq_len = EXCLUDED.seq_len,
                    horizon_days = EXCLUDED.horizon_days,
                    rise_threshold = EXCLUDED.rise_threshold,
                    created_at = now()
                """,
                [(cutoff, code, float(probability), args.run_name, args.seq_len, args.horizon_days, args.rise_threshold) for code, probability in rows],
            )
        connection.commit()
        print(f"saved {len(rows)} rows to {spec.prediction_table}")
    finally:
        connection.close()


def run_prediction(spec: PredictionSpec, weekly: bool = False) -> None:
    args = parse_args(spec)
    base_module = importlib.import_module(f"create_model.{spec.base_module}")
    v2_module = importlib.import_module(f"create_model.{spec.v2_module}")
    database_config = static.db_config_jp if spec.market == "JP" else static.db_config_kr
    raw_columns = list(base_module._RAW_COLS)
    excluded_columns = {"LowerBand60_3"} if weekly else set()
    codes = [args.code] if args.code else base_module.load_codes(args.table, args.start_date, args.end_date)
    if not codes:
        raise RuntimeError("no codes loaded from database")

    history_days = max(args.seq_len, ICHIMOKU_HISTORY_DAYS if args.require_above_ichimoku_cloud else 0)
    connection = postgres.connect(**database_config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT MAX(date) FROM {args.table}")
            row = cursor.fetchone()
    finally:
        connection.close()
    requested_cutoff = pd.to_datetime(args.as_of).date().isoformat()
    cutoff = min(requested_cutoff, row[0].isoformat()) if row and row[0] else requested_cutoff

    model = base_module.StockTransformer(input_size=len(v2_module.V2_FEATURE_COLS), d_model=args.d_model, nhead=args.nhead, num_encoder_layers=args.num_encoder_layers, dim_feedforward=args.dim_feedforward, dropout=args.dropout)
    checkpoint_loader = importlib.import_module("create_model.model_jp").load_model_checkpoint
    model_mode = spec.model_mode or getattr(v2_module, "MODEL_MODE", v2_module.V2_MODEL_MODE)
    model.load_state_dict(checkpoint_loader(args.model, model_mode, map_location="cpu"))
    model.eval()

    results: list[tuple[str, float]] = []
    connection = postgres.connect(**database_config)
    try:
        for index, code in enumerate(codes, start=1):
            if index == 1 or index % max(1, args.log_every) == 0:
                print(f"[infer-v2] code={code} ({index})")
            raw_history = _fetch_raw_history(connection, args.table, code, history_days, cutoff, raw_columns, excluded_columns)
            if raw_history is not None and _passes_selection_filters(raw_history, raw_columns, args):
                sequence = v2_module.compute_v2_features(raw_history)[-args.seq_len:]
                with torch.no_grad():
                    logit = model(torch.from_numpy(sequence[None, ...])).item()
                    results.append((code, float(torch.sigmoid(torch.tensor(logit)).item())))
    finally:
        connection.close()

    results.sort(key=lambda item: item[1], reverse=True)
    if args.min_prob is not None:
        results = [item for item in results if item[1] >= args.min_prob]
    selected = results[:args.top_k]
    print("code,upside_probability")
    for code, probability in selected:
        print(f"{code},{probability:.6f}")
    if args.save_db and selected:
        args.run_name = args.run_name or Path(args.model).name
        _save_predictions(spec, args, selected, cutoff)
