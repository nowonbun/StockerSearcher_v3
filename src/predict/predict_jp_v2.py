from common import PredictionSpec, run_prediction

if __name__ == "__main__":
    run_prediction(PredictionSpec(
        "JP", "STOCK_DATA_JP", "stock_predict_jp", "model_jp", "model_jp_v2", "model_jp_v2.pt", 60, 20, 0.05, 50,
        default_min_prob=0.55,
        default_require_ma20_above_ma60=True,
        default_require_above_ichimoku_cloud=True,
    ))
