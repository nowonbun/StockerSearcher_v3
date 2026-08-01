from common import PredictionSpec, run_prediction
from create_model.split_source import daily_split_source


def build_prediction_spec() -> PredictionSpec:
    return PredictionSpec(
        "KR", daily_split_source("KR"), "stock_predict_kr", "model_kr", "model_kr_v3", "model_kr_v3.pt", 60, 20, 0.05, 50,
        default_min_prob=0.25, default_require_ma20_above_ma60=True, default_require_above_ichimoku_cloud=True, default_require_upper_band_breakout=True, default_max_extension_ratio=0.50,
        model_mode="v3_trend_filtered_upside_probability_kr",
    )


if __name__ == "__main__":
    run_prediction(build_prediction_spec())
