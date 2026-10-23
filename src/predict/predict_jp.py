"""JP 일봉 예측의 통합 실행 진입점."""

from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from predict.common import PredictionSpec, run_prediction_cli
from create_model.split_source import daily_split_source


def build_prediction_spec(model_version: str = "v3") -> PredictionSpec:
    if model_version not in ("v2", "v3"):
        raise ValueError(f"지원하지 않는 예측 모델 버전: {model_version}")
    trend_filtered = model_version == "v3"
    return PredictionSpec(
        market="JP",
        table=daily_split_source("JP") if trend_filtered else "STOCK_DATA_JP",
        prediction_table="stock_predict_jp",
        base_module="model_jp",
        v2_module="model_jp",
        model_file=f"model_jp_{model_version}.pt",
        seq_len=60,
        horizon_days=20,
        rise_threshold=0.05,
        top_k=50,
        default_min_prob=0.25,
        default_require_ma20_above_ma60=True,
        default_require_above_ichimoku_cloud=True,
        default_require_upper_band_breakout=trend_filtered,
        default_max_extension_ratio=0.50 if trend_filtered else None,
        model_version=model_version,
        model_mode="v3_trend_filtered_upside_probability_jp" if trend_filtered else None,
    )


def main(argv=None) -> None:
    run_prediction_cli(build_prediction_spec, weekly=False, argv=argv)


if __name__ == "__main__":
    main()
