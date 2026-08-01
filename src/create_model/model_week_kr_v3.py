from __future__ import annotations

from pathlib import Path

from . import model_week_kr_v2 as base
from .model_week_v3_features import WEEK_V3_FEATURE_COLS, compute_week_v3_features, eligible_week_v3
from .split_source import training_cutoff_date, weekly_split_source
import function.static as static


V3_MODEL_MODE = "v3_trend_filtered_upside_probability_week_kr"
MODEL_MODE = V3_MODEL_MODE

_base_features = base.compute_v2_features


def compute_v3_features(raw):
    return compute_week_v3_features(raw, _base_features, base._RAW_COLS)


V2_FEATURE_COLS = WEEK_V3_FEATURE_COLS
compute_v2_features = compute_v3_features


def main() -> None:
    original_parse_args = base.parse_args
    base.compute_v2_features = compute_v3_features
    base.V2_FEATURE_COLS = WEEK_V3_FEATURE_COLS
    base.V2_MODEL_MODE = V3_MODEL_MODE
    base.sample_eligibility = lambda raw, end: eligible_week_v3(raw, end, base._RAW_COLS)

    def parse_args_v3():
        args = original_parse_args()
        if args.table == "STOCK_DATA_WEEK_KR":
            args.table = weekly_split_source("KR")
        if args.end_date == static.end_date:
            args.end_date = training_cutoff_date()
        if args.model_out.endswith("model_week_kr_v2.pt"):
            args.model_out = str(Path(__file__).resolve().parents[1] / "models" / "model_week_kr_v3.pt")
        return args

    base.parse_args = parse_args_v3
    base.main()


if __name__ == "__main__":
    main()
