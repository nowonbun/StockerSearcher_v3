from common import PredictionSpec, run_prediction

if __name__ == "__main__":
    run_prediction(PredictionSpec(
        "JP", "STOCK_DATA_WEEK_JP", "stock_predict_week_jp", "model_week_jp", "model_week_jp_v2", "model_week_jp_v2.pt", 120, 20, 0.09, 30,
        default_min_prob=0.25,
        default_require_ma20_above_ma60=True,
        default_require_above_ichimoku_cloud=False,
    ), weekly=True)
