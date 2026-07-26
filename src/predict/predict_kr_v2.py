from common import PredictionSpec, run_prediction

if __name__ == "__main__":
    run_prediction(PredictionSpec(
        "KR", "STOCK_DATA_KR", "stock_predict_kr", "model_kr", "model_kr_v2", "model_kr_v2.pt", 60, 20, 0.05, 50,
        default_min_prob=0.55,
        default_require_ma20_above_ma60=True,
        default_require_above_ichimoku_cloud=True,
    ))
