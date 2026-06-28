# -*- coding: utf-8 -*-
"""Tests for portfolio digest module — date support, freshness, and formatting."""

import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch


class TestPortfolioDigest(unittest.TestCase):
    """Tests for src/services/portfolio_digest.py."""

    # ---- Parsing ----

    def test_parse_date_valid(self):
        from src.services.portfolio_digest import _parse_date
        self.assertEqual(_parse_date("2026-06-27"), date(2026, 6, 27))
        self.assertEqual(_parse_date("20260627"), date(2026, 6, 27))

    def test_parse_date_invalid(self):
        from src.services.portfolio_digest import _parse_date
        self.assertIsNone(_parse_date("not-a-date"))
        self.assertIsNone(_parse_date(""))
        self.assertIsNone(_parse_date(None))

    # ---- Formatting ----

    def test_format_analyses_with_freshness(self):
        from src.services.portfolio_digest import format_analyses_for_prompt

        reports = [
            {"code": "NVDA", "name": "NVIDIA", "sentiment_score": 85,
             "operation_advice": "持有", "trend_prediction": "看多",
             "analysis_summary": "AI demand strong"},
            {"code": "AAPL", "name": "苹果", "sentiment_score": 60,
             "operation_advice": "观望", "trend_prediction": "横盘",
             "analysis_summary": "Valuation high"},
        ]
        freshness = [
            {"code": "NVDA", "name": "NVIDIA", "report_date": "2026-06-27", "age_days": 0, "fresh": True},
            {"code": "AAPL", "name": "苹果", "report_date": "2026-06-26", "age_days": 1, "fresh": False},
        ]

        result = format_analyses_for_prompt(reports, freshness)
        self.assertIn("今日", result)
        self.assertIn("昨日", result)
        self.assertIn("NVDA", result)
        self.assertIn("AAPL", result)

    def test_format_analyses_old_report(self):
        from src.services.portfolio_digest import format_analyses_for_prompt

        reports = [{"code": "TSLA", "name": "Tesla", "sentiment_score": 40,
                     "operation_advice": "减持", "trend_prediction": "下跌",
                     "analysis_summary": "Weak deliveries"}]
        freshness = [{"code": "TSLA", "name": "Tesla", "report_date": "2026-06-24",
                       "age_days": 3, "fresh": False}]

        result = format_analyses_for_prompt(reports, freshness)
        self.assertIn("3天前", result)
        self.assertIn("2026-06-24", result)

    # ---- Freshness computation ----

    def test_compute_freshness_all_fresh(self):
        from src.services.portfolio_digest import compute_stock_freshness

        target = date(2026, 6, 27)
        reports = [
            {"code": "NVDA", "name": "NVIDIA", "created_at": "2026-06-27T10:00:00"},
            {"code": "AAPL", "name": "苹果", "created_at": "2026-06-27T09:00:00"},
        ]
        result = compute_stock_freshness(reports, target)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r["fresh"] for r in result))
        self.assertFalse(any(r["age_days"] > 0 for r in result))

    def test_compute_freshness_mixed(self):
        from src.services.portfolio_digest import compute_stock_freshness

        target = date(2026, 6, 27)
        reports = [
            {"code": "NVDA", "name": "NVIDIA", "created_at": "2026-06-27T10:00:00"},
            {"code": "AAPL", "name": "苹果", "created_at": "2026-06-25T10:00:00"},
        ]
        result = compute_stock_freshness(reports, target)
        fresh = [r for r in result if r["fresh"]]
        stale = [r for r in result if not r["fresh"]]
        self.assertEqual(len(fresh), 1)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["age_days"], 2)

    def test_compute_freshness_invalid_date(self):
        from src.services.portfolio_digest import compute_stock_freshness

        target = date(2026, 6, 27)
        reports = [{"code": "NVDA", "name": "NVIDIA", "created_at": "garbage"}]
        result = compute_stock_freshness(reports, target)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["age_days"], 0)  # falls back to target_date

    # ---- Available dates ----

    def test_get_available_dates_empty(self):
        from src.services.portfolio_digest import get_available_dates

        with patch("src.storage.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.get_analysis_history.return_value = []
            mock_get_db.return_value = mock_db
            result = get_available_dates()
        self.assertEqual(result, [])

    def test_get_available_dates_with_data(self):
        from src.services.portfolio_digest import get_available_dates

        mock_records = []
        for day in (27, 26, 25):
            r = MagicMock()
            r.created_at = datetime(2026, 6, day, 10, 0, 0)
            r.report_type = "analysis"
            mock_records.append(r)

        with patch("src.storage.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.get_analysis_history.return_value = mock_records
            mock_get_db.return_value = mock_db
            result = get_available_dates()

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "2026-06-27")  # newest first

    # ---- Fetch by date ----

    def test_fetch_reports_for_date(self):
        from src.services.portfolio_digest import fetch_reports_for_date

        mock_record = MagicMock()
        mock_record.report_type = "analysis"
        mock_record.to_dict.return_value = {
            "code": "NVDA", "name": "NVIDIA", "report_type": "analysis",
            "sentiment_score": 85, "operation_advice": "持有",
            "trend_prediction": "看多", "analysis_summary": "Good",
            "created_at": "2026-06-27T10:00:00",
        }
        mock_record.created_at = datetime(2026, 6, 27, 10, 0, 0)

        with patch("src.storage.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.get_analysis_history.return_value = [mock_record]
            mock_get_db.return_value = mock_db
            reports, effective_date = fetch_reports_for_date(target_date=date(2026, 6, 27))

        self.assertEqual(len(reports), 1)
        self.assertEqual(effective_date, date(2026, 6, 27))
        self.assertEqual(reports[0]["code"], "NVDA")

    # ---- Full digest generation (mocked LLM) ----

    @patch("src.services.portfolio_digest.litellm")
    @patch("src.services.portfolio_digest._resolve_model")
    def test_generate_digest_with_freshness(self, mock_resolve, mock_litellm):
        from src.services.portfolio_digest import generate_portfolio_digest
        import src.services.portfolio_digest as pd_module
        pd_module._HAS_LITELLM = True

        mock_resolve.return_value = "openai/test-model"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Portfolio is healthy."
        mock_response.model = "openai/test-model"
        mock_litellm.completion.return_value = mock_response

        mock_reports = [
            {"code": "NVDA", "name": "NVIDIA", "report_type": "analysis",
             "sentiment_score": 80, "operation_advice": "持有",
             "trend_prediction": "看多", "analysis_summary": "OK",
             "created_at": "2026-06-27T10:00:00"},
            {"code": "AAPL", "name": "苹果", "report_type": "analysis",
             "sentiment_score": 60, "operation_advice": "观望",
             "trend_prediction": "横盘", "analysis_summary": "High PE",
             "created_at": "2026-06-26T10:00:00"},
        ]

        with patch("src.services.portfolio_digest.fetch_reports_for_date",
                   return_value=(mock_reports, date(2026, 6, 27))):
            result = generate_portfolio_digest(target_date="2026-06-27")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["target_date"], "2026-06-27")
        self.assertTrue(result["any_stale"])
        self.assertEqual(len(result["stocks_freshness"]), 2)
        self.assertEqual(result["stocks_freshness"][0]["fresh"], True)   # NVDA
        self.assertEqual(result["stocks_freshness"][1]["fresh"], False)  # AAPL
        call_args = mock_litellm.completion.call_args
        prompt = call_args[1]["messages"][0]["content"]
        self.assertIn("不是最新的", prompt)

    @patch("src.services.portfolio_digest._resolve_model")
    def test_generate_digest_no_data(self, mock_resolve):
        from src.services.portfolio_digest import generate_portfolio_digest

        with patch("src.storage.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.get_analysis_history.return_value = []
            mock_get_db.return_value = mock_db
            result = generate_portfolio_digest(target_date="2026-01-01")

        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["target_date"], "2026-01-01")
        self.assertFalse(result["is_today"])

    @patch("src.services.portfolio_digest._resolve_model")
    def test_generate_digest_no_model(self, mock_resolve):
        from src.services.portfolio_digest import generate_portfolio_digest

        mock_resolve.return_value = ""
        mock_record = MagicMock()
        mock_record.report_type = "analysis"
        mock_record.to_dict.return_value = {
            "code": "NVDA", "name": "NVIDIA", "report_type": "analysis",
            "sentiment_score": 85, "operation_advice": "持有",
            "trend_prediction": "看多", "analysis_summary": "Good",
            "created_at": "2026-06-27T10:00:00",
        }
        mock_record.created_at = datetime(2026, 6, 27, 10, 0, 0)

        with patch("src.storage.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.get_analysis_history.return_value = [mock_record]
            mock_get_db.return_value = mock_db
            result = generate_portfolio_digest()

        self.assertEqual(result["error"], "No LLM model configured. Set LITELLM_MODEL in .env.")
        self.assertEqual(len(result["stocks_freshness"]), 1)

    @patch("src.services.portfolio_digest.litellm")
    @patch("src.services.portfolio_digest._resolve_model")
    def test_generate_digest_llm_error(self, mock_resolve, mock_litellm):
        from src.services.portfolio_digest import generate_portfolio_digest
        import src.services.portfolio_digest as pd_module
        pd_module._HAS_LITELLM = True

        mock_resolve.return_value = "openai/test-model"
        mock_litellm.completion.side_effect = RuntimeError("API timeout")

        mock_reports = [{
            "code": "NVDA", "name": "NVIDIA", "report_type": "analysis",
            "sentiment_score": 85, "operation_advice": "持有",
            "trend_prediction": "看多", "analysis_summary": "Good",
            "created_at": "2026-06-27T10:00:00",
        }]

        with patch("src.services.portfolio_digest.fetch_reports_for_date",
                   return_value=(mock_reports, date(2026, 6, 27))):
            result = generate_portfolio_digest(target_date="2026-06-27")

        self.assertEqual(result["status"], "error")
        self.assertIn("LLM generation failed", result["error"])
        self.assertEqual(len(result["stocks_freshness"]), 1)
        self.assertFalse(result["any_stale"])


if __name__ == "__main__":
    unittest.main()
