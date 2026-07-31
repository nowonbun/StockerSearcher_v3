from __future__ import annotations

import os
import sys
import time
import types
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("STOCK_DB_PASSWORD", "test-password")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from dataset import split_dataset  # noqa: E402


class SplitDatasetTests(unittest.TestCase):
    @staticmethod
    def _jp_import_modules(driver: MagicMock) -> dict[str, types.ModuleType]:
        selenium = types.ModuleType("selenium")
        webdriver = types.ModuleType("selenium.webdriver")
        webdriver_chrome = types.ModuleType("selenium.webdriver.chrome")
        webdriver_service = types.ModuleType("selenium.webdriver.chrome.service")
        webdriver.ChromeOptions = MagicMock(return_value=MagicMock())
        webdriver.Chrome = MagicMock(return_value=driver)
        webdriver_service.Service = MagicMock(return_value=MagicMock())
        selenium.webdriver = webdriver
        webdriver.chrome = webdriver_chrome
        webdriver_chrome.service = webdriver_service

        webdriver_manager = types.ModuleType("webdriver_manager")
        webdriver_manager_chrome = types.ModuleType("webdriver_manager.chrome")
        webdriver_manager_chrome.ChromeDriverManager = MagicMock()
        webdriver_manager.chrome = webdriver_manager_chrome
        return {
            "selenium": selenium,
            "selenium.webdriver": webdriver,
            "selenium.webdriver.chrome": webdriver_chrome,
            "selenium.webdriver.chrome.service": webdriver_service,
            "webdriver_manager": webdriver_manager,
            "webdriver_manager.chrome": webdriver_manager_chrome,
        }

    def test_tables_selects_market_and_frequency_specific_tables(self) -> None:
        self.assertEqual(split_dataset._tables("KR", False).ohlcv, "stock_ohlcv_kr")
        self.assertEqual(split_dataset._tables("jp", True).indicators, "stock_indicator_week_jp")
        with self.assertRaisesRegex(ValueError, "Unsupported market: US"):
            split_dataset._tables("US", False)

    def test_recent_collection_window_uses_the_default_one_calendar_month(self) -> None:
        self.assertEqual(
            split_dataset.recent_collection_window(date(2026, 1, 31)),
            ("2025-12-31", "2026-01-31"),
        )

    def test_recent_collection_window_uses_configurable_calendar_months(self) -> None:
        self.assertEqual(
            split_dataset.recent_collection_window(date(2026, 3, 31), months=2),
            ("2026-01-31", "2026-03-31"),
        )

    def test_indicator_payload_keeps_only_the_latest_collection_month(self) -> None:
        rows = [
            (pd.Timestamp("2026-01-26"), *range(1, 15)),
            (pd.Timestamp("2026-02-02"), *range(1, 15)),
            (pd.Timestamp("2026-02-27"), *range(1, 15)),
        ]
        self.assertEqual(
            split_dataset._indicator_rows_for_update(rows, date(2026, 2, 27), months=1),
            rows[1:],
        )

    def test_kr_rows_excludes_zero_volume_and_aggregates_weekly(self) -> None:
        index = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"])
        frame = pd.DataFrame(
            {
                "Open": [100.2, 101.2, 200.2],
                "High": [110.2, 120.2, 210.2],
                "Low": [90.2, 95.2, 190.2],
                "Close": [105.2, 115.2, 205.2],
                "Volume": [10.0, 0.0, 30.0],
            },
            index=index,
        )

        self.assertEqual(
            split_dataset._kr_rows(frame, weekly=False),
            [("2026-01-01", 100, 110, 90, 105, 10), ("2026-01-05", 200, 210, 190, 205, 30)],
        )
        self.assertEqual(
            split_dataset._kr_rows(frame, weekly=True),
            [("2026-01-02", 100, 120, 90, 115, 10), ("2026-01-09", 200, 210, 190, 205, 30)],
        )

    def test_kr_rows_returns_empty_rows_for_none_or_empty_frame(self) -> None:
        self.assertEqual(split_dataset._kr_rows(None, weekly=False), [])
        self.assertEqual(split_dataset._kr_rows(pd.DataFrame(), weekly=True), [])

    def test_kr_rows_rejects_frames_missing_required_ohlcv_columns(self) -> None:
        frame = pd.DataFrame({"Open": [1], "High": [2], "Low": [0], "Volume": [3]})

        with self.assertRaisesRegex(ValueError, "Close"):
            split_dataset._kr_rows(frame, weekly=False)

    def test_jp_rows_filters_zero_volume_and_normalizes_weekly_date(self) -> None:
        timestamp = int(datetime(2026, 1, 7, tzinfo=timezone.utc).timestamp() * 1000)
        raw = {
            "timestamp": [timestamp, timestamp],
            "open": [100.2, 200.2],
            "high": [110.2, 210.2],
            "low": [90.2, 190.2],
            "close": [105.2, 205.2],
            "volume": [12.0, 0.0],
        }

        self.assertEqual(split_dataset._jp_rows(raw, weekly=True), [("2026-01-05", 100, 110, 90, 105, 12)])

    def test_jp_rows_excludes_candles_with_missing_price_or_volume(self) -> None:
        timestamp = int(datetime(2026, 1, 7, tzinfo=timezone.utc).timestamp() * 1000)
        raw = {
            "timestamp": [timestamp, timestamp, timestamp],
            "open": [100.0, None, 300.0],
            "high": [110.0, 210.0, 310.0],
            "low": [90.0, 190.0, 290.0],
            "close": [105.0, 205.0, 305.0],
            "volume": [12.0, 22.0, None],
        }

        self.assertEqual(split_dataset._jp_rows(raw, weekly=False), [("2026-01-07", 100, 110, 90, 105, 12)])

    def test_round_or_none_handles_missing_and_numeric_values(self) -> None:
        self.assertIsNone(split_dataset._round_or_none(float("nan")))
        self.assertEqual(split_dataset._round_or_none(12.6), 13)

    def test_indicator_rows_preserve_dates_and_emit_nulls_before_windows_complete(self) -> None:
        dates = pd.date_range("2025-01-01", periods=80, freq="D")
        frame = pd.DataFrame(
            {
                "date": dates,
                "high": range(2, 82),
                "low": range(0, 80),
                "close": range(1, 81),
            }
        )

        rows = split_dataset._indicator_rows(frame)

        self.assertEqual(len(rows), 80)
        self.assertEqual(rows[0][0], dates[0])
        self.assertEqual(rows[0][1], None)  # 5-day moving average
        self.assertEqual(rows[59][4], 30)  # 60-day moving average for 1..60
        self.assertEqual(rows[79][7], 68)  # 60-day upper Bollinger band for linear data

    def test_indicator_rows_keeps_ichimoku_window_and_shift_boundaries(self) -> None:
        dates = pd.date_range("2025-01-01", periods=78, freq="D")
        frame = pd.DataFrame({"date": dates, "high": range(2, 80), "low": range(78), "close": range(1, 79)})

        rows = split_dataset._indicator_rows(frame)

        self.assertIsNone(rows[7][10])  # conversion: before the 9-row window
        self.assertEqual(rows[8][10], 5)
        self.assertIsNone(rows[24][11])  # base: before the 26-row window
        self.assertEqual(rows[25][11], 14)
        self.assertIsNone(rows[50][12])  # span A: before 26-row shift completes
        self.assertEqual(rows[51][12], 18)
        self.assertIsNone(rows[76][13])  # span B: before 52-row window plus shift completes
        self.assertEqual(rows[77][13], 26)
        self.assertEqual(rows[51][14], 78)  # lagging: final non-null source value
        self.assertIsNone(rows[52][14])

    def test_indicator_rows_processes_ten_thousand_rows_as_a_local_benchmark(self) -> None:
        dates = pd.date_range("2000-01-01", periods=10_000, freq="D")
        frame = pd.DataFrame({"date": dates, "high": range(2, 10_002), "low": range(10_000), "close": range(1, 10_001)})

        started = time.perf_counter()
        rows = split_dataset._indicator_rows(frame)
        elapsed_seconds = time.perf_counter() - started

        self.assertEqual(len(rows), 10_000)
        self.assertGreaterEqual(elapsed_seconds, 0.0)

    def test_upsert_skips_database_connection_for_empty_payload(self) -> None:
        with patch.object(split_dataset.psycopg, "connect") as connect:
            split_dataset._upsert_ohlcv("KR", False, "005930", [])
        connect.assert_not_called()

    def test_upsert_uses_market_table_and_code_prefixed_payload(self) -> None:
        cursor = MagicMock()
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        with patch.object(split_dataset.psycopg, "connect", return_value=connection) as connect:
            split_dataset._upsert_ohlcv("JP", True, "7203", [("2026-01-05", 1, 2, 0, 1, 9)])

        connect.assert_called_once_with(**split_dataset.static.db_config)
        query, payload = cursor.executemany.call_args.args
        self.assertIn("INSERT INTO stock_ohlcv_week_jp", query)
        self.assertIn("ON CONFLICT (code, date) DO UPDATE", query)
        self.assertEqual(payload, [("7203", "2026-01-05", 1, 2, 0, 1, 9)])

    def test_upsert_selects_the_ohlcv_table_for_every_market_and_frequency(self) -> None:
        expected_tables = {
            ("KR", False): "stock_ohlcv_kr",
            ("KR", True): "stock_ohlcv_week_kr",
            ("JP", False): "stock_ohlcv_jp",
            ("JP", True): "stock_ohlcv_week_jp",
        }
        for (market, weekly), table in expected_tables.items():
            with self.subTest(market=market, weekly=weekly):
                cursor = MagicMock()
                connection = MagicMock()
                connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
                with patch.object(split_dataset.psycopg, "connect", return_value=connection):
                    split_dataset._upsert_ohlcv(market, weekly, "code", [("2026-01-05", 1, 2, 0, 1, 9)])

                query, _ = cursor.executemany.call_args.args
                self.assertIn(f"INSERT INTO {table}", query)

    def test_calculate_indicators_does_not_open_code_connections_for_empty_code_list(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        with patch.object(split_dataset.psycopg, "connect", return_value=connection) as connect:
            split_dataset.calculate_indicators("KR", weekly=False)

        connect.assert_called_once_with(**split_dataset.static.db_config)
        query = cursor.execute.call_args.args[0]
        self.assertIn("SELECT DISTINCT code FROM stock_ohlcv_kr", query)
        cursor.executemany.assert_not_called()

    def test_calculate_indicators_skips_insert_when_indicator_payload_is_empty(self) -> None:
        codes_cursor = MagicMock()
        codes_cursor.fetchall.return_value = [("005930",)]
        codes_connection = MagicMock()
        codes_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = codes_cursor
        rows_cursor = MagicMock()
        rows_cursor.fetchall.return_value = []
        rows_connection = MagicMock()
        rows_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = rows_cursor
        with (
            patch.object(split_dataset.psycopg, "connect", side_effect=[codes_connection, rows_connection]) as connect,
            patch.object(split_dataset, "_indicator_rows", return_value=[]) as indicator_rows,
        ):
            split_dataset.calculate_indicators("KR", weekly=False)

        self.assertEqual(connect.call_count, 2)
        indicator_rows.assert_not_called()
        self.assertIn("FROM stock_ohlcv_kr WHERE code = %s", rows_cursor.execute.call_args.args[0])
        rows_cursor.executemany.assert_not_called()

    def test_calculate_indicators_daily_mode_never_queries_or_inserts_weekly_tables(self) -> None:
        codes_cursor = MagicMock()
        codes_cursor.fetchall.return_value = [("005930",)]
        codes_connection = MagicMock()
        codes_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = codes_cursor
        rows_cursor = MagicMock()
        rows_cursor.fetchall.return_value = []
        rows_connection = MagicMock()
        rows_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = rows_cursor

        with (
            patch.object(split_dataset.psycopg, "connect", side_effect=[codes_connection, rows_connection]),
            patch.object(split_dataset, "_indicator_rows", return_value=[]),
        ):
            split_dataset.calculate_indicators("KR", weekly=False)

        executed_sql = "\n".join(call.args[0] for call in (codes_cursor.execute.call_args_list + rows_cursor.execute.call_args_list))
        self.assertIn("stock_ohlcv_kr", executed_sql)
        self.assertNotIn("stock_ohlcv_week_kr", executed_sql)
        self.assertNotIn("stock_indicator_week_kr", executed_sql)

    def test_calculate_indicators_inserts_code_prefixed_sixteen_value_payload(self) -> None:
        codes_cursor = MagicMock()
        codes_cursor.fetchall.return_value = [("005930",)]
        codes_connection = MagicMock()
        codes_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = codes_cursor
        rows_cursor = MagicMock()
        rows_cursor.fetchall.return_value = [(date(2026, 1, 5), 1, 2, 0, 1, 9)]
        rows_connection = MagicMock()
        rows_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = rows_cursor
        insert_cursor = MagicMock()
        insert_connection = MagicMock()
        insert_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = insert_cursor
        indicator_row = (pd.Timestamp("2026-01-05"), *range(1, 15))

        with (
            patch.object(split_dataset.psycopg, "connect", side_effect=[codes_connection, rows_connection, insert_connection]),
            patch.object(split_dataset, "_indicator_rows", return_value=[indicator_row]),
        ):
            split_dataset.calculate_indicators("KR", weekly=False)

        query, payload = insert_cursor.executemany.call_args.args
        self.assertEqual(payload, [("005930", *indicator_row)])
        self.assertEqual(len(payload[0]), 16)
        self.assertEqual(query.split("VALUES", 1)[1].split("now()", 1)[0].count("%s"), 16)
        self.assertIn("LIMIT %s", rows_cursor.execute.call_args.args[0])
        self.assertEqual(rows_cursor.execute.call_args.args[1], ("005930", split_dataset.static.indicator_history_rows))

    def test_calculate_indicators_upserts_only_the_latest_configured_month(self) -> None:
        codes_cursor = MagicMock()
        codes_cursor.fetchall.return_value = [("005930",)]
        codes_connection = MagicMock()
        codes_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = codes_cursor
        rows_cursor = MagicMock()
        rows_cursor.fetchall.return_value = [(date(2026, 2, 27), 1, 2, 0, 1, 9)]
        rows_connection = MagicMock()
        rows_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = rows_cursor
        insert_cursor = MagicMock()
        insert_connection = MagicMock()
        insert_connection.__enter__.return_value.cursor.return_value.__enter__.return_value = insert_cursor
        rows = [
            (pd.Timestamp("2026-01-26"), *range(1, 15)),
            (pd.Timestamp("2026-02-02"), *range(1, 15)),
            (pd.Timestamp("2026-02-27"), *range(1, 15)),
        ]

        with (
            patch.object(split_dataset.psycopg, "connect", side_effect=[codes_connection, rows_connection, insert_connection]),
            patch.object(split_dataset, "_indicator_rows", return_value=rows),
            patch.object(split_dataset.static, "split_collection_months", 1),
            patch.object(split_dataset.static, "indicator_history_rows", 300),
        ):
            split_dataset.calculate_indicators("KR", weekly=False)

        _, payload = insert_cursor.executemany.call_args.args
        self.assertEqual(payload, [("005930", *row) for row in rows[1:]])

    def test_collect_kr_refreshes_codes_before_daily_then_weekly_upserts(self) -> None:
        events: list[tuple[str, object]] = []
        frame = pd.DataFrame({"Open": [1], "High": [2], "Low": [0], "Close": [1], "Volume": [1]}, index=pd.to_datetime(["2026-01-05"]))
        executor = MagicMock()
        executor.__enter__.return_value = executor

        def map_synchronously(function: object, codes: object) -> list[None]:
            return [function(code) for code in codes]  # type: ignore[operator]

        executor.map.side_effect = map_synchronously
        with (
            patch.object(split_dataset.dataset_kr, "save_stock_list", side_effect=lambda: events.append(("save", ""))),
            patch.object(split_dataset.dataset_kr, "get_stock_list", side_effect=lambda: events.append(("get", "")) or [("005930",)]),
            patch.object(split_dataset.fdr, "DataReader", side_effect=lambda code, start, end: events.append(("read", (code, start, end))) or frame),
            patch.object(split_dataset, "ThreadPoolExecutor", return_value=executor),
            patch.object(split_dataset, "_upsert_ohlcv", side_effect=lambda market, weekly, code, rows: events.append(("upsert", (market, weekly, code, list(rows))))),
            patch.object(split_dataset, "recent_collection_window", return_value=("2026-01-01", "2026-01-31")),
        ):
            split_dataset.collect_kr(weekly=True)

        self.assertEqual([event[0] for event in events], ["save", "get", "read", "upsert", "upsert"])
        self.assertEqual(events[2][1], ("005930", "2026-01-01", "2026-01-31"))
        self.assertEqual(events[3][1][1], False)
        self.assertEqual(events[4][1][1], True)

    def test_collect_jp_quits_driver_on_success_and_fetch_failure(self) -> None:
        for failure in (False, True):
            with self.subTest(fetch_failure=failure):
                driver = MagicMock()
                modules = self._jp_import_modules(driver)
                fetch = MagicMock()
                if failure:
                    fetch.side_effect = RuntimeError("fetch failed")
                else:
                    fetch.return_value = {"raw": "value"}
                with (
                    patch.dict(sys.modules, modules),
                    patch("os.path.exists", return_value=True),
                    patch.object(split_dataset.dataset_jp, "save_stock_list", return_value=[types.SimpleNamespace(code="7203")]),
                    patch.object(split_dataset.dataset_jp, "fetch_stock_raw", fetch),
                    patch.object(split_dataset, "_jp_rows", return_value=[]),
                    patch.object(split_dataset, "_upsert_ohlcv"),
                ):
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, "fetch failed"):
                            split_dataset.collect_jp(weekly=True)
                    else:
                        split_dataset.collect_jp(weekly=True)
                driver.quit.assert_called_once_with()

    def test_collect_jp_daily_mode_upserts_no_weekly_rows(self) -> None:
        driver = MagicMock()
        modules = self._jp_import_modules(driver)
        upsert = MagicMock()
        with (
            patch.dict(sys.modules, modules),
            patch("os.path.exists", return_value=True),
            patch.object(split_dataset.dataset_jp, "save_stock_list", return_value=[types.SimpleNamespace(code="7203")]),
            patch.object(split_dataset.dataset_jp, "fetch_stock_raw", return_value={"raw": "value"}) as fetch,
            patch.object(split_dataset, "_jp_rows", return_value=[("2026-01-05", 1, 2, 0, 1, 9)]),
            patch.object(split_dataset, "_upsert_ohlcv", upsert),
            patch.object(split_dataset.static, "split_collection_months", 2),
        ):
            split_dataset.collect_jp(weekly=False)

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[2:4], (split_dataset.stock_lib.PERIOD_TYPE_MONTH, 2))
        self.assertEqual(upsert.call_count, 1)
        self.assertEqual(upsert.call_args.args[:2], ("JP", False))
        driver.quit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
