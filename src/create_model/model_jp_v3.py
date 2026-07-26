from __future__ import annotations

from pathlib import Path

from . import model_jp_v2 as base
from .model_v3_features import V3_FEATURE_COLS, compute_v3_features as build_v3_features, eligible_v3

V3_MODEL_MODE = "v3_trend_filtered_upside_probability_jp"
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
        if args.model_out.endswith("model_jp_v2.pt"):
            args.model_out = str(Path(__file__).resolve().parents[1] / "models" / "model_jp_v3.pt")
        return args
    base.parse_args = parse_args_v3
    base.main()


if __name__ == "__main__":
    main()
