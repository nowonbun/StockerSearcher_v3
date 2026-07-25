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
import postgre as postgres


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


def _not_null_clause(columns: Iterable[str], excluded: set[str]) -> str:
    return " AND ".join(f"{column} IS NOT NULL" for column in columns if column not in excluded)


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
    parser.add_argument("--min-prob", type=float, default=None)
    parser.add_argument("--log-every", type=int, default=200)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--code", default=None)
    return parser.parse_args()


def _fetch_sequence(connection: postgres.connector.Connection, table: str, code: str, seq_len: int, cutoff: str | None, raw_columns: list[str], feature_builder, excluded_columns: set[str]) -> np.ndarray | None:
    where = f"code = %s AND {_not_null_clause(raw_columns, excluded_columns)}"
    params: tuple[object, ...] = (code, seq_len)
    if cutoff:
        where = f"code = %s AND date <= %s AND {_not_null_clause(raw_columns, excluded_columns)}"
        params = (code, cutoff, seq_len)
    query = f"SELECT {', '.join(raw_columns)} FROM {table} WHERE {where} ORDER BY date DESC LIMIT %s"
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    return None if len(rows) < seq_len else feature_builder(np.array(rows[::-1], dtype=np.float32))


def _save_predictions(spec: PredictionSpec, args: argparse.Namespace, rows: list[tuple[str, float]], cutoff: str) -> None:
    connection = postgres.connector.connect(**(static.db_config_jp if spec.market == "JP" else static.db_config_kr))
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
    base_module = importlib.import_module(spec.base_module)
    v2_module = importlib.import_module(spec.v2_module)
    database_config = static.db_config_jp if spec.market == "JP" else static.db_config_kr
    raw_columns = list(base_module._RAW_COLS)
    excluded_columns = {"LowerBand60_3"} if weekly else set()
    codes = [args.code] if args.code else base_module.load_codes(args.table, args.start_date, args.end_date)
    if not codes:
        raise RuntimeError("no codes loaded from database")

    connection = postgres.connector.connect(**database_config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT MAX(date) FROM {args.table}")
            row = cursor.fetchone()
    finally:
        connection.close()
    requested_cutoff = pd.to_datetime(args.as_of).date().isoformat()
    cutoff = min(requested_cutoff, row[0].isoformat()) if row and row[0] else requested_cutoff

    model = base_module.StockTransformer(input_size=len(v2_module.V2_FEATURE_COLS), d_model=args.d_model, nhead=args.nhead, num_encoder_layers=args.num_encoder_layers, dim_feedforward=args.dim_feedforward, dropout=args.dropout)
    checkpoint_loader = importlib.import_module("model_jp").load_model_checkpoint
    model.load_state_dict(checkpoint_loader(args.model, v2_module.V2_MODEL_MODE, map_location="cpu"))
    model.eval()

    results: list[tuple[str, float]] = []
    connection = postgres.connector.connect(**database_config)
    try:
        for index, code in enumerate(codes, start=1):
            if index == 1 or index % max(1, args.log_every) == 0:
                print(f"[infer-v2] code={code} ({index})")
            sequence = _fetch_sequence(connection, args.table, code, args.seq_len, cutoff, raw_columns, v2_module.compute_v2_features, excluded_columns)
            if sequence is not None:
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
