"""웹 스캐너와 MCP 후보 조회의 계약을 DB 호출 없이 검증한다."""

import asyncio
import datetime as dt
from decimal import Decimal
import unittest
from unittest.mock import patch

import mcp_server as server


class RecommendationTests(unittest.TestCase):
    def test_daily_and_weekly_history_use_split_views_and_keep_query_contract(self):
        for market, code in (("KR", "005930"), ("JP", "9432")):
            for weekly in (False, True):
                for limit in (1, 0, -1):
                    with self.subTest(market=market, weekly=weekly, limit=limit):
                        history = server.stock_data_week if weekly else server.stock_data
                        fixture = [{"code": code, "date": "2026-10-23", "close": 175}]
                        with patch.object(server, "_rows", return_value=fixture) as rows:
                            result = history(market.lower(), code, limit=limit, start_date="2026-10-01", end_date="2026-10-23")
                        table = f"stock_data_split_{'week_' if weekly else ''}{market.lower()}"
                        expected = f"SELECT * FROM {table} WHERE code = %s AND date >= %s AND date <= %s ORDER BY date DESC"
                        params = (code, "2026-10-01", "2026-10-23")
                        if limit > 0:
                            expected += " LIMIT %s"
                            params += (limit,)
                        rows.assert_called_once_with(expected, params)
                        self.assertEqual(result, fixture)

    def test_ntt_history_and_recommendation_read_the_same_split_date_fixture(self):
        fixture = {"date": "2026-10-23", "code": "9432", "name": "ＮＴＴ", "close": 175,
                   "band": 173, "ma60": 170, "transAmnt": 43389920000, "ratio": Decimal("1.01156")}

        def stored_rows(query, params):
            # 구형 테이블에는 당일 행이 없고 분리 뷰에만 있는 상황을 재현한다.
            if "FROM stock_data_split_jp" in query:
                return [dict(fixture)]
            return []

        with patch.object(server, "_rows", side_effect=stored_rows):
            candidate = server.recommend_stocks("JP", "2026-10-23", limit=1)["rows"][0]
            history = server.stock_data("JP", "9432", start_date="2026-10-23", end_date="2026-10-23", limit=1)
        self.assertEqual(len(history), 1)
        for field in ("code", "date", "close"):
            self.assertEqual(history[0][field], candidate[field])

    def test_history_defaults_empty_results_and_database_errors_are_preserved(self):
        for history, default_limit in ((server.stock_data, 2000), (server.stock_data_week, 500)):
            with self.subTest(history=history.__name__):
                with patch.object(server, "_rows", return_value=[]) as rows:
                    self.assertEqual(history("JP", "9432"), [])
                self.assertEqual(rows.call_args.args[1], ("9432", default_limit))
                self.assertNotIn("date >=", rows.call_args.args[0])
                self.assertNotIn("date <=", rows.call_args.args[0])
                with patch.object(server, "_rows", side_effect=RuntimeError("database unavailable")) as rows:
                    with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                        history("JP", "9432")
                    self.assertEqual(rows.call_count, 1)

    def test_registered_tools_replace_predictions_only(self):
        names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
        self.assertEqual(names, {"readme", "usage", "list_stocks", "stock_data", "stock_data_week", "recommend_stocks"})

    def test_readme_is_self_contained_and_never_connects_to_db(self):
        with patch.object(server, "_rows") as rows, patch.object(server.psycopg, "connect") as connect:
            result = server.readme()
        rows.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(result["server"], "stocksearcher-postgres-mcp")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["markets"], ["KR", "JP"])
        self.assertIn("실시간", result["limitations"])
        self.assertIn("수익률", result["limitations"])
        self.assertIn("usage", result["next_step"])

    def test_usage_describes_all_registered_tools_with_actual_schemas(self):
        with patch.object(server, "_rows") as rows, patch.object(server.psycopg, "connect") as connect:
            result = asyncio.run(server.usage())
        rows.assert_not_called()
        connect.assert_not_called()
        registered = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        self.assertEqual(set(result["tools"]), set(registered))
        for name, tool in registered.items():
            with self.subTest(tool=name):
                entry = result["tools"][name]
                self.assertEqual(entry["parameters"], tool.inputSchema)
                self.assertEqual(entry["description"], tool.description)
                self.assertTrue(entry["examples"])
        recommendation = result["tools"]["recommend_stocks"]
        self.assertIn("date", recommendation["parameters"]["properties"])
        self.assertEqual(recommendation["examples"][0]["date"], "2026-10-23")
        self.assertIn("두 번째", result["notes"]["recommend_stocks"])

    def test_usage_selects_one_tool_and_rejects_unknown_tool(self):
        result = asyncio.run(server.usage("recommend_stocks"))
        self.assertEqual(set(result["tools"]), {"recommend_stocks"})
        for name in ("unknown", "predict_rows", "list_predict_dates", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                asyncio.run(server.usage(name))

    def test_initial_server_instructions_point_ai_to_help_tools(self):
        self.assertIn("readme", server.mcp.instructions)
        self.assertIn("usage", server.mcp.instructions)

    def test_all_market_period_and_band_combinations_match_web(self):
        for market in ("KR", "JP"):
            for weekly in (False, True):
                for mode in ("upper", "lower"):
                    with self.subTest(market=market, weekly=weekly, mode=mode):
                        fixture = [{"date": dt.date(2026, 10, 23), "code": "TEST", "name": "테스트", "close": 100, "band": 90, "ma60": 110, "transAmnt": 9000000000, "ratio": 100 / 90}]
                        with patch.object(server, "_rows", return_value=fixture) as rows:
                            result = server.recommend_stocks(market, "2026-10-23", weekly=weekly, mode=mode, limit=7)
                        query, params = rows.call_args.args
                        table = f"stock_data_split_{'week_' if weekly else ''}{market.lower()}"
                        self.assertIn(f"FROM {table} sd", query)
                        self.assertIn(f"JOIN stock_list_{market.lower()} sl", query)
                        self.assertIn("ORDER BY sd.transamnt DESC", query)
                        self.assertIn("sd.transamnt > %s AND sd.close < %s", query)
                        self.assertEqual("sl.stocktype != 'ETF・ETN'" in query, market == "JP")
                        condition = 'sd.lowerband60_1 < sd.close AND sd.close < sd."60mvavg"' if mode == "lower" else 'sd.close > sd.upperband60_1'
                        self.assertIn(condition, query)
                        amount = (2500000000 if weekly else 500000000) if market == "JP" else (5000000000 if weekly else 1000000000)
                        self.assertEqual(params, ("2026-10-23", amount, 1000 if market == "JP" else 100000, 7))
                        row = result["rows"][0]
                        self.assertEqual(row["rank"], 1)
                        self.assertEqual(row["date"], "2026-10-23")
                        self.assertEqual(row["name"], "테스트")
                        self.assertEqual(row[f"{mode}band60_1"], 90)
                        self.assertIn("reason", row)
                        self.assertEqual("ma60" in row, mode == "lower")

    def test_defaults_use_web_second_latest_date_in_thirty_day_window(self):
        with patch.object(server, "_rows", side_effect=[[{"date": dt.date(2026, 10, 23)}, {"date": dt.date(2026, 10, 22)}], []]) as rows:
            result = server.recommend_stocks()
        self.assertIn("CURRENT_DATE - INTERVAL '30 days'", rows.call_args_list[0].args[0])
        self.assertEqual(rows.call_args_list[1].args[1][0], "2026-10-22")
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["date"], "2026-10-22")

    def test_single_default_date_and_no_data(self):
        with patch.object(server, "_rows", side_effect=[[{"date": "2026-10-23"}], []]):
            self.assertEqual(server.recommend_stocks()["date"], "2026-10-23")
        with patch.object(server, "_rows", return_value=[]) as rows:
            self.assertIsNone(server.recommend_stocks()["date"])
            self.assertEqual(rows.call_count, 1)

    def test_explicit_filters_are_parameterized_including_zero(self):
        with patch.object(server, "_rows", return_value=[]) as rows:
            server.recommend_stocks("kr", "2026-10-23", trans_amnt_min=0, close_max=12345, limit=1)
        self.assertEqual(rows.call_args.args[1], ("2026-10-23", 0, 12345, 1))

    def test_ranks_and_numeric_values_preserve_database_order(self):
        fixture = [
            {"date": dt.date(2026, 10, 23), "code": "B", "name": "나", "close": Decimal("110"), "band": Decimal("100"), "ma60": 120, "transAmnt": 9000000000, "ratio": Decimal("1.1")},
            {"date": dt.date(2026, 10, 23), "code": "A", "name": "가", "close": Decimal("120"), "band": Decimal("100"), "ma60": 130, "transAmnt": 8000000000, "ratio": Decimal("1.2")},
        ]
        with patch.object(server, "_rows", return_value=fixture):
            result = server.recommend_stocks(date="2026-10-23")
        self.assertEqual([row["code"] for row in result["rows"]], ["B", "A"])
        self.assertEqual([row["rank"] for row in result["rows"]], [1, 2])
        self.assertEqual(result["rows"][0]["upperband_ratio"], 1.1)
        self.assertEqual(result["rows"][0]["close"], 110.0)

    def test_invalid_inputs_never_query_database(self):
        cases = [{"market": "US"}, {"mode": "anything"}, {"date": "2026-02-30"}, {"date": "20261023"}, {"date": "2026-10-23' OR 1=1"}, {"limit": 0}, {"limit": 501}, {"limit": 1.5}, {"limit": True}, {"trans_amnt_min": float("nan")}, {"close_max": float("inf")}]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), patch.object(server, "_rows") as rows:
                with self.assertRaises(ValueError):
                    server.recommend_stocks(**kwargs)
                rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
