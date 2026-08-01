from __future__ import annotations

import sys
import unittest
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from create_model.split_source import daily_split_source, weekly_split_source


class SplitSourceTests(unittest.TestCase):
    def test_daily_split_source_uses_the_market_compatibility_view(self) -> None:
        self.assertEqual(daily_split_source("JP"), "stock_data_split_jp")
        self.assertEqual(daily_split_source("kr"), "stock_data_split_kr")

    def test_daily_split_source_rejects_unsupported_market(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported market: US"):
            daily_split_source("US")

    def test_weekly_split_source_uses_the_market_compatibility_view(self) -> None:
        self.assertEqual(weekly_split_source("JP"), "stock_data_split_week_jp")
        self.assertEqual(weekly_split_source("kr"), "stock_data_split_week_kr")

    def test_v3_prediction_modules_export_the_features_expected_by_common_runner(self) -> None:
        for module_name in ("create_model.model_jp_v3", "create_model.model_kr_v3"):
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(hasattr(module, "V2_FEATURE_COLS"))
                self.assertTrue(callable(getattr(module, "compute_v2_features", None)))

    def test_v3_prediction_specs_use_the_matching_daily_compatibility_views(self) -> None:
        predict_dir = str(Path(__file__).resolve().parents[1] / "src" / "predict")
        sys.path.insert(0, predict_dir)
        try:
            cases = (("predict_jp_v3", "JP", "stock_data_split_jp"), ("predict_kr_v3", "KR", "stock_data_split_kr"))
            for module_name, market, table in cases:
                with self.subTest(module=module_name):
                    module = importlib.import_module(module_name)
                    spec = module.build_prediction_spec()
                    self.assertEqual(spec.market, market)
                    self.assertEqual(spec.table, table)
                    self.assertEqual(spec.v2_module, f"model_{market.lower()}_v3")
        finally:
            sys.path.remove(predict_dir)

    def test_weekly_v3_specs_use_weekly_views_without_ichimoku_filters(self) -> None:
        predict_dir = str(Path(__file__).resolve().parents[1] / "src" / "predict")
        sys.path.insert(0, predict_dir)
        try:
            cases = (("JP", "stock_data_split_week_jp"), ("KR", "stock_data_split_week_kr"))
            for market, table in cases:
                with self.subTest(market=market):
                    model = importlib.import_module(f"create_model.model_week_{market.lower()}_v3")
                    prediction = importlib.import_module(f"predict_week_{market.lower()}_v3")
                    spec = prediction.build_prediction_spec()
                    self.assertEqual(spec.table, table)
                    self.assertFalse(spec.default_require_above_ichimoku_cloud)
                    self.assertFalse(any("cloud" in feature for feature in model.V2_FEATURE_COLS))
        finally:
            sys.path.remove(predict_dir)


if __name__ == "__main__":
    unittest.main()
