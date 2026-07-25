from common import PredictionSpec, run_prediction

if __name__ == "__main__":
    run_prediction(PredictionSpec("JP", "STOCK_DATA_JP", "stock_predict_jp", "model_jp", "model_jp_v2", "model_jp_v2.pt", 60, 20, 0.05, 50))
