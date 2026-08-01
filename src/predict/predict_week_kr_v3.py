from common import PredictionSpec, run_prediction
from create_model.split_source import weekly_split_source


def build_prediction_spec() -> PredictionSpec:
    return PredictionSpec(
        "KR", weekly_split_source("KR"), "stock_predict_week_kr", "model_week_kr", "model_week_kr_v3", "model_week_kr_v3.pt", 120, 20, 0.09, 30,
        default_min_prob=0.25, default_require_ma20_above_ma60=True,
        default_require_above_ichimoku_cloud=False, default_require_upper_band_breakout=True,
        default_max_extension_ratio=0.50, model_mode="v3_trend_filtered_upside_probability_week_kr",
    )


if __name__ == "__main__":
    run_prediction(build_prediction_spec(), weekly=True)
