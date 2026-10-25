"""웹 스캐너와 MCP 후보 조회의 계약을 DB 호출 없이 검증한다."""

import asyncio
import datetime as dt
from decimal import Decimal
import unittest
from unittest.mock import patch

import mcp_server as server


class RecommendationTests(unittest.TestCase):
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
