from __future__ import annotations

from pathlib import Path

from . import model_kr_v2 as base
from .model_v3_features import V3_FEATURE_COLS, compute_v3_features as build_v3_features, eligible_v3
from .split_source import daily_split_source, training_cutoff_date
import function.static as static

V3_MODEL_MODE = "v3_trend_filtered_upside_probability_kr"
MODEL_MODE = V3_MODEL_MODE

_base_features = base.compute_v2_features
def compute_v3_features(raw):
    return build_v3_features(raw, _base_features, base._RAW_COLS)


def main() -> None:
    original_parse_args = base.parse_args
    base.compute_v2_features = compute_v3_features
    base.V2_FEATURE_COLS = V3_FEATURE_COLS
    base.V2_MODEL_MODE = V3_MODEL_MODE
    base.sample_eligibility = lambda raw, end: eligible_v3(raw, end, base._RAW_COLS)
    def parse_args_v3():
        args = original_parse_args()
        if args.table == "STOCK_DATA_KR":
            args.table = daily_split_source("KR")
        if args.end_date == static.end_date:
            args.end_date = training_cutoff_date()
        if args.model_out.endswith("model_kr_v2.pt"):
            args.model_out = str(Path(__file__).resolve().parents[1] / "models" / "model_kr_v3.pt")
        return args
    base.parse_args = parse_args_v3
    base.main()


if __name__ == "__main__":
    main()
