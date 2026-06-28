# -*- coding: utf-8 -*-
"""Tests for portfolio digest module."""

import unittest
from unittest.mock import MagicMock, patch


class TestPortfolioDigest(unittest.TestCase):
    """Tests for src/services/portfolio_digest.py."""

    def test_format_analyses_for_prompt(self):
        """format_analyses_for_prompt produces correct structure."""
        from src.services.portfolio_digest import format_analyses_for_prompt

        reports = [
            {
                "code": "NVDA",
                "name": "NVIDIA",
                "sentiment_score": 85,
                "operation_advice": "持有",
                "trend_prediction": "短期震荡偏多",
                "analysis_summary": "AI 需求强劲，但短期超买",
            },
            {
                "code": "AAPL",
                "name": "苹果",
                "sentiment_score": 60,
                "operation_advice": "观望",
                "trend_prediction": "横盘整理",
                "analysis_summary": "估值偏高，等待回调",
            },
        ]

        result = format_analyses_for_prompt(reports, lang="zh")

        self.assertIn("#1 NVDA NVIDIA", result)
        self.assertIn("评分: 85/100", result)
        self.assertIn("建议: 持有", result)
        self.assertIn("AI 需求强劲", result)
        self.assertIn("#2 AAPL 苹果", result)
        self.assertIn("评分: 60/100", result)

    def test_format_missing_fields(self):
        """format_analyses_for_prompt handles missing fields gracefully."""
        from src.services.portfolio_digest import format_analyses_for_prompt

        reports = [{"code": "TEST", "name": None}]
        result = format_analyses_for_prompt(reports)

        self.assertIn("#1 TEST", result)
        self.assertIn("未评分", result)
        self.assertIn("未提供", result)

    def test_fetch_todays_reports_empty(self):
        """fetch_todays_reports returns empty list when no data."""
        from src.services.portfolio_digest import fetch_todays_reports

        with patch("src.storage.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.get_analysis_history.return_value = []
            mock_get_db.return_value = mock_db
            result = fetch_todays_reports()
        self.assertEqual(result, [])

    @patch("src.services.portfolio_digest._resolve_model")
    def test_generate_digest_no_data(self, mock_resolve):
        """generate_portfolio_digest returns no_data when no reports exist."""
        from src.services.portfolio_digest import generate_portfolio_digest

        with patch("src.services.portfolio_digest.fetch_todays_reports", return_value=[]):
            result = generate_portfolio_digest()

        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["stock_count"], 0)
        self.assertIsNone(result["digest_text"])

    @patch("src.services.portfolio_digest._resolve_model")
    def test_generate_digest_no_model(self, mock_resolve):
        """generate_portfolio_digest returns error when no model configured."""
        from src.services.portfolio_digest import generate_portfolio_digest

        mock_resolve.return_value = ""
        reports = [{"code": "NVDA", "name": "NVIDIA", "sentiment_score": 85,
                     "operation_advice": "持有", "trend_prediction": "看多",
                     "analysis_summary": "Good", "created_at": "2026-01-01"}]

        with patch("src.services.portfolio_digest.fetch_todays_reports", return_value=reports):
            result = generate_portfolio_digest()

        self.assertEqual(result["status"], "error")
        self.assertIn("No LLM model", result["error"])

    @patch("src.services.portfolio_digest.litellm")
    @patch("src.services.portfolio_digest._resolve_model")
    def test_generate_digest_success(self, mock_resolve, mock_litellm):
        """generate_portfolio_digest returns ok with LLM output."""
        from src.services.portfolio_digest import generate_portfolio_digest
        import src.services.portfolio_digest as pd_module
        pd_module._HAS_LITELLM = True

        mock_resolve.return_value = "openai/test-model"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Portfolio summary text here."
        mock_response.model = "openai/test-model"
        mock_litellm.completion.return_value = mock_response

        reports = [{
            "code": "NVDA", "name": "NVIDIA", "sentiment_score": 85,
            "operation_advice": "持有", "trend_prediction": "看多",
            "analysis_summary": "Good", "created_at": "2026-01-01",
        }]

        with patch("src.services.portfolio_digest.fetch_todays_reports", return_value=reports):
            result = generate_portfolio_digest()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stock_count"], 1)
        self.assertIn("Portfolio summary", result["digest_text"])
        self.assertEqual(result["stocks_included"], ["NVDA"])
        self.assertEqual(result["model_used"], "openai/test-model")
        mock_litellm.completion.assert_called_once()

    @patch("src.services.portfolio_digest.litellm")
    @patch("src.services.portfolio_digest._resolve_model")
    def test_generate_digest_llm_error(self, mock_resolve, mock_litellm):
        """generate_portfolio_digest handles LLM errors gracefully."""
        from src.services.portfolio_digest import generate_portfolio_digest
        import src.services.portfolio_digest as pd_module
        pd_module._HAS_LITELLM = True

        mock_resolve.return_value = "openai/test-model"
        mock_litellm.completion.side_effect = RuntimeError("API timeout")

        reports = [{"code": "NVDA", "name": "NVIDIA", "sentiment_score": 85,
                     "operation_advice": "持有", "trend_prediction": "看多",
                     "analysis_summary": "Good", "created_at": "2026-01-01"}]

        with patch("src.services.portfolio_digest.fetch_todays_reports", return_value=reports):
            result = generate_portfolio_digest()

        self.assertEqual(result["status"], "error")
        self.assertIn("LLM generation failed", result["error"])


if __name__ == "__main__":
    unittest.main()
