from common import PredictionSpec, run_prediction

if __name__ == "__main__":
    run_prediction(PredictionSpec("KR", "STOCK_DATA_WEEK_KR", "stock_predict_week_kr", "model_week_kr", "model_week_kr_v2", "model_week_kr_v2.pt", 120, 20, 0.09, 30), weekly=True)
