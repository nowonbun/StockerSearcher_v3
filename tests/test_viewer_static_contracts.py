from __future__ import annotations

import unittest
from pathlib import Path


VIEWER_ROOT = Path(__file__).resolve().parents[1] / "src" / "viewer"
API_ROOT = VIEWER_ROOT / "server" / "api"
UTILS_ROOT = VIEWER_ROOT / "server" / "utils"


class ViewerStaticContractTests(unittest.TestCase):
    def test_every_api_route_delegates_to_its_expected_stock_api_endpoint(self) -> None:
        expected = {
            "lowerband-scanner-weekly.get.ts": "lowerband-scanner-weekly",
            "lowerband-scanner.get.ts": "lowerband-scanner",
            "predict-dates-weekly.get.ts": "predict-dates-weekly",
            "predict-dates.get.ts": "predict-dates",
            "predict-weekly.get.ts": "predict-weekly",
            "predict.get.ts": "predict",
            "scanner-dates.get.ts": "scanner-dates",
            "scanner-defaults.get.ts": "scanner-defaults",
            "scanner-weekly-dates.get.ts": "scanner-weekly-dates",
            "scanner-weekly-defaults.get.ts": "scanner-weekly-defaults",
            "scanner-weekly.get.ts": "scanner-weekly",
            "scanner.get.ts": "scanner",
            "series-weekly.get.ts": "series-weekly",
            "series.get.ts": "series",
        }
        self.assertEqual({path.name for path in API_ROOT.glob("*.ts")}, set(expected))
        for filename, endpoint in expected.items():
            with self.subTest(filename=filename):
                text = (API_ROOT / filename).read_text(encoding="utf-8")
                self.assertIn("import { handleStockApi }", text)
                self.assertIn(f"handleStockApi(event, '{endpoint}')", text)

    def test_stock_api_keeps_query_validation_and_parameterized_database_calls(self) -> None:
        text = (UTILS_ROOT / "stock-api.ts").read_text(encoding="utf-8")

        for required in (
            "market must be JP or KR",
            "must be YYYY-MM-DD",
            "code must contain 1-20 letters, numbers, dot, underscore, or hyphen",
            "must be numeric",
            "queryRows<Record<string, unknown>>",
            "[asOf, asOf]",
            "[date, transAmount, closeMax]",
            "[code, asOf, weekly ? 120 : 240]",
            "API endpoint not found",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_stock_api_reads_display_data_from_split_compatibility_views(self) -> None:
        text = (UTILS_ROOT / "stock-api.ts").read_text(encoding="utf-8")

        for required in (
            "data: 'stock_data_split_jp'",
            "data: 'stock_data_split_kr'",
            "weeklyData: 'stock_data_split_week_jp'",
            "weeklyData: 'stock_data_split_week_kr'",
            "NULL::numeric AS di_plus",
            "NULL::numeric AS di_minus",
            "NULL::numeric AS adx",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_database_utility_requires_password_and_normalizes_date_and_numeric_values(self) -> None:
        text = (UTILS_ROOT / "db.ts").read_text(encoding="utf-8")

        for required in (
            "if (!password) throw createError",
            "STOCK_DB_PASSWORD is not configured",
            "query<T>(text, values)",
            "value.toISOString().slice(0, 10)",
            "return value === null || value === undefined ? null : Number(value)",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
