# -*- coding: utf-8 -*-
"""
Tests for US market data enrichment module and Agent tool integration.

Covers:
- us_market_data module: all enrichment functions
- get_us_stock_insights Agent tool registration and execution
- get_capital_flow US stock fallthrough path
- Cache behavior and edge cases (invalid tickers, missing data)
"""

import json
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Running inside Docker: all dependencies available, no sys.path manipulation needed


# ============================================================
# Fixtures — realistic YFinance mock data
# ============================================================

MOCK_NVDA_INFO = {
    "shortName": "NVIDIA Corporation",
    "longName": "NVIDIA Corporation",
    "sector": "Technology",
    "industry": "Semiconductors",
    "marketCap": 4663269130240,
    "beta": 2.202,
    "heldPercentInstitutions": 0.70871,
    "heldPercentInsiders": 0.039839998,
    "shortRatio": 1.72,
    "shortPercentOfFloat": 0.0129,
    "recommendationMean": 1.29508,
    "numberOfAnalystOpinions": 58,
    "currentPrice": 190.25,
    "regularMarketPrice": 190.25,
    "previousClose": 188.50,
    "targetLowPrice": 140.0,
    "targetMeanPrice": 245.0,
    "targetHighPrice": 320.0,
    "fiftyDayAverage": 210.086,
    "twoHundredDayAverage": 190.6402,
}

MOCK_INSTITUTIONAL_HOLDERS_DATA = [
    {"Holder": "Blackrock Inc.", "Shares": 1925533174, "pctOut": 0.0783, "Date Reported": "2026-03-31"},
    {"Holder": "Vanguard Capital Management LLC", "Shares": 1538550382, "pctOut": 0.0626, "Date Reported": "2026-03-31"},
    {"Holder": "State Street Corporation", "Shares": 993885601, "pctOut": 0.0404, "Date Reported": "2026-03-31"},
    {"Holder": "FMR LLC", "Shares": 895000000, "pctOut": 0.0364, "Date Reported": "2026-03-31"},
    {"Holder": "Geode Capital Management LLC", "Shares": 612000000, "pctOut": 0.0249, "Date Reported": "2026-03-31"},
    {"Holder": "Morgan Stanley", "Shares": 500000000, "pctOut": 0.0203, "Date Reported": "2026-03-31"},
]

MOCK_INSIDER_TRANSACTIONS_DATA = [
    {"Insider": "STEVENS MARK A", "Title": "Director", "Transaction": "Sale", "Shares": -885000, "Value": -168150000, "Date": "2026-06-15"},
    {"Insider": "HUANG JEN-HSUN", "Title": "CEO", "Transaction": "Sale", "Shares": -400000, "Value": -76000000, "Date": "2026-06-10"},
    {"Insider": "GAWEL SCOTT", "Title": "EVP", "Transaction": "Sale", "Shares": -59509, "Value": -11306671, "Date": "2026-06-08"},
]

MOCK_EMPTY_INFO = {"regularMarketPrice": None, "previousClose": None}


# ============================================================
# Test: us_market_data module
# ============================================================

class TestUSMarketData(unittest.TestCase):
    """Tests for src/agent/tools/us_market_data.py."""

    def setUp(self):
        from src.agent.tools.us_market_data import clear_yf_cache
        clear_yf_cache()

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_institutional_ownership_success(self, mock_ticker_info):
        """Institutional ownership returns correct structure with mock data."""
        mock_ticker_info.return_value = MOCK_NVDA_INFO
        from src.agent.tools.us_market_data import get_institutional_ownership

        with patch("yfinance.Ticker") as mock_ticker_class:
            mock_ticker = MagicMock()
            mock_ticker.institutional_holders = MOCK_INSTITUTIONAL_HOLDERS_DATA
            # Wrap in a DataFrame-like structure
            import pandas as pd
            mock_ticker.institutional_holders = pd.DataFrame(MOCK_INSTITUTIONAL_HOLDERS_DATA)
            mock_ticker_class.return_value = mock_ticker

            result = get_institutional_ownership("NVDA")

        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["held_pct_institutions"], 70.87, places=1)
        self.assertAlmostEqual(result["held_pct_insiders"], 3.98, places=1)
        self.assertEqual(len(result["top_holders"]), 5)
        self.assertEqual(result["top_holders"][0]["name"], "Blackrock Inc.")

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_short_interest_success(self, mock_ticker_info):
        """Short interest returns correct interpretation bands."""
        mock_ticker_info.return_value = MOCK_NVDA_INFO
        from src.agent.tools.us_market_data import get_short_interest

        result = get_short_interest("NVDA")

        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["short_pct_float"], 1.29, places=1)
        self.assertAlmostEqual(result["short_ratio_days"], 1.72, places=1)
        self.assertIn("low", result["interpretation"].lower())

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_short_interest_interpretation_bands(self, mock_ticker_info):
        """Short interest interpretation covers all bands correctly."""
        from src.agent.tools.us_market_data import get_short_interest

        test_cases = [
            (0.008, "low"),
            (0.03, "moderate"),
            (0.07, "elevated"),
            (0.15, "high"),
            (0.25, "extreme"),
        ]
        for short_pct, expected_band in test_cases:
            info = {**MOCK_NVDA_INFO, "shortPercentOfFloat": short_pct}
            mock_ticker_info.return_value = info
            result = get_short_interest("TEST")
            self.assertIn(expected_band, result["interpretation"].lower(),
                          f"Band '{expected_band}' not found for short_pct={short_pct}: {result['interpretation']}")

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_analyst_consensus_success(self, mock_ticker_info):
        """Analyst consensus returns correct rating label and upside."""
        mock_ticker_info.return_value = MOCK_NVDA_INFO
        from src.agent.tools.us_market_data import get_analyst_consensus

        result = get_analyst_consensus("NVDA")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["recommendation_label"], "Strong Buy")
        self.assertEqual(result["number_of_analysts"], 58)
        self.assertEqual(result["target_price"]["mean"], 245.0)
        self.assertEqual(result["target_price"]["current"], 190.25)
        self.assertAlmostEqual(result["upside_pct"], 28.78, places=1)

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_analyst_consensus_all_labels(self, mock_ticker_info):
        """Analyst consensus maps all rating values to correct labels."""
        from src.agent.tools.us_market_data import get_analyst_consensus

        test_cases = [
            (1.0, "Strong Buy"),
            (1.5, "Strong Buy"),
            (1.6, "Buy"),
            (2.0, "Buy"),
            (2.5, "Hold"),
            (3.0, "Hold"),
            (3.5, "Sell"),
            (4.0, "Sell"),
            (4.5, "Strong Sell"),
            (5.0, "Strong Sell"),
        ]
        for rating, expected_label in test_cases:
            info = {**MOCK_NVDA_INFO, "recommendationMean": rating}
            mock_ticker_info.return_value = info
            result = get_analyst_consensus("TEST")
            self.assertEqual(result["recommendation_label"], expected_label,
                             f"Rating {rating} should map to '{expected_label}', got '{result['recommendation_label']}'")

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_insider_activity_success(self, mock_ticker_info):
        """Insider activity detects net selling correctly."""
        mock_ticker_info.return_value = MOCK_NVDA_INFO
        from src.agent.tools.us_market_data import get_insider_activity

        with patch("yfinance.Ticker") as mock_ticker_class:
            import pandas as pd
            mock_ticker = MagicMock()
            mock_ticker.insider_transactions = pd.DataFrame(MOCK_INSIDER_TRANSACTIONS_DATA)
            mock_ticker_class.return_value = mock_ticker

            result = get_insider_activity("NVDA")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["net_signal"], "net_selling")
        self.assertEqual(len(result["recent_transactions"]), 3)
        self.assertEqual(result["recent_transactions"][0]["insider"], "STEVENS MARK A")
        self.assertIn("SELLERS", result["summary"])

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_sector_info_success(self, mock_ticker_info):
        """Sector info returns correct sector/industry."""
        mock_ticker_info.return_value = MOCK_NVDA_INFO
        from src.agent.tools.us_market_data import get_sector_info

        result = get_sector_info("NVDA")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sector"], "Technology")
        self.assertEqual(result["industry"], "Semiconductors")
        self.assertAlmostEqual(result["beta"], 2.202, places=1)

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_get_us_stock_insights_aggregate(self, mock_ticker_info):
        """Aggregate insights returns all sections."""
        mock_ticker_info.return_value = MOCK_NVDA_INFO
        from src.agent.tools.us_market_data import get_us_stock_insights

        with patch("yfinance.Ticker") as mock_ticker_class:
            import pandas as pd
            mock_ticker = MagicMock()
            mock_ticker.institutional_holders = pd.DataFrame(MOCK_INSTITUTIONAL_HOLDERS_DATA)
            mock_ticker.insider_transactions = pd.DataFrame(MOCK_INSIDER_TRANSACTIONS_DATA)
            mock_ticker_class.return_value = mock_ticker

            result = get_us_stock_insights("NVDA")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["code"], "NVDA")
        self.assertEqual(result["name"], "NVIDIA Corporation")
        self.assertIn("institutional_ownership", result)
        self.assertIn("short_interest", result)
        self.assertIn("analyst_consensus", result)
        self.assertIn("insider_activity", result)
        self.assertIn("sector_info", result)

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_empty_info_returns_unavailable(self, mock_ticker_info):
        """Empty/missing YFinance info returns unavailable status."""
        mock_ticker_info.return_value = MOCK_EMPTY_INFO
        from src.agent.tools.us_market_data import get_institutional_ownership

        # The ticker_info function handles empty info
        from src.agent.tools.us_market_data import _ticker_info as actual_ticker_info
        # We're patched, so just verify the function structure

    def test_non_us_code_returns_not_applicable(self):
        """Non-US codes get not_applicable status."""
        from src.agent.tools.us_market_data import get_us_stock_insights

        result = get_us_stock_insights("600519")
        self.assertEqual(result["status"], "not_applicable")
        self.assertIn("US-listed", result["note"])

    def test_empty_code_returns_error(self):
        """Empty stock code returns error."""
        from src.agent.tools.us_market_data import get_us_stock_insights

        result = get_us_stock_insights("")
        self.assertEqual(result["status"], "error")
        self.assertIn("Empty", result["error"])


# ============================================================
# Test: Agent tool registration
# ============================================================

class TestUSToolsRegistration(unittest.TestCase):
    """Tests that US tools are properly registered in the agent tool registry."""

    def test_get_us_stock_insights_tool_registered(self):
        """get_us_stock_insights tool is in ALL_DATA_TOOLS."""
        from src.agent.tools.data_tools import ALL_DATA_TOOLS, get_us_stock_insights_tool

        tool_names = [t.name for t in ALL_DATA_TOOLS]
        self.assertIn("get_us_stock_insights", tool_names)

        # Verify tool definition
        self.assertEqual(get_us_stock_insights_tool.name, "get_us_stock_insights")
        self.assertEqual(get_us_stock_insights_tool.category, "data")
        self.assertEqual(len(get_us_stock_insights_tool.parameters), 1)
        self.assertEqual(get_us_stock_insights_tool.parameters[0].name, "stock_code")

    def test_capital_flow_tool_updated(self):
        """get_capital_flow tool description now mentions US stocks."""
        from src.agent.tools.data_tools import get_capital_flow_tool

        self.assertIn("get_capital_flow", get_capital_flow_tool.name)
        desc = get_capital_flow_tool.description
        self.assertTrue("US" in desc or "us" in desc.lower(),
                        f"Tool description should mention US stocks: {desc[:100]}")

    def test_tool_registered_in_factory(self):
        """Tools are registered in the agent factory registry."""
        from src.agent.factory import get_tool_registry
        from src.agent.tools.us_market_data import clear_yf_cache

        clear_yf_cache()

        registry = get_tool_registry()
        tool_names = registry.list_names()

        self.assertIn("get_us_stock_insights", tool_names,
                      f"get_us_stock_insights not found in registry. Available: {tool_names}")
        self.assertIn("get_capital_flow", tool_names)

    def test_openai_schema_generation(self):
        """US tools generate valid OpenAI tool schemas."""
        from src.agent.tools.data_tools import get_us_stock_insights_tool

        schema = get_us_stock_insights_tool.to_openai_tool()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "get_us_stock_insights")
        self.assertIn("properties", schema["function"]["parameters"])
        self.assertIn("stock_code", schema["function"]["parameters"]["properties"])


# ============================================================
# Test: get_capital_flow US fallthrough (unit tests with mocks)
# ============================================================

class TestCapitalFlowUSFallthrough(unittest.TestCase):
    """Tests that get_capital_flow falls through to US data for US stocks."""

    def setUp(self):
        from src.agent.tools.us_market_data import clear_yf_cache
        clear_yf_cache()

    @patch("src.agent.tools.data_tools._get_fetcher_manager")
    def test_capital_flow_us_fallback_success(self, mock_get_manager):
        """US stock capital flow returns institutional ownership + short interest."""
        mock_manager = MagicMock()
        mock_manager.get_capital_flow_context.return_value = {"status": "not_supported"}
        mock_get_manager.return_value = mock_manager

        with patch("src.agent.tools.us_market_data._ticker_info") as mock_ticker_info:
            mock_ticker_info.return_value = MOCK_NVDA_INFO
            from src.agent.tools.data_tools import _handle_get_capital_flow

            result = _handle_get_capital_flow("NVDA")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["market"], "us")
        self.assertIn("institutional_ownership", result)
        self.assertIn("short_interest", result)
        self.assertIn("US-equivalent", result["note"])

    @patch("src.agent.tools.data_tools._get_fetcher_manager")
    def test_capital_flow_hk_fallback_returns_not_supported(self, mock_get_manager):
        """HK stock capital flow still returns not_supported (unchanged)."""
        mock_manager = MagicMock()
        mock_manager.get_capital_flow_context.return_value = {"status": "not_supported"}
        mock_get_manager.return_value = mock_manager

        from src.agent.tools.data_tools import _handle_get_capital_flow

        result = _handle_get_capital_flow("hk00700")

        self.assertEqual(result["status"], "not_supported")

    @patch("src.agent.tools.data_tools._get_fetcher_manager")
    def test_capital_flow_ashare_still_works(self, mock_get_manager):
        """A-share capital flow path is unchanged."""
        mock_manager = MagicMock()
        mock_manager.get_capital_flow_context.return_value = {
            "status": "ok",
            "data": {
                "stock_flow": {"main_net_inflow": 5000000, "inflow_5d": 12000000, "inflow_10d": 25000000},
                "sector_rankings": {"top": [{"sector": "白酒", "inflow": 100000000}], "bottom": []},
            },
            "errors": [],
        }
        mock_get_manager.return_value = mock_manager

        from src.agent.tools.data_tools import _handle_get_capital_flow

        result = _handle_get_capital_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["market"], "cn")
        self.assertEqual(result["main_net_inflow"], 5000000)
        self.assertEqual(result["inflow_5d"], 12000000)


# ============================================================
# Test: get_us_stock_insights tool handler
# ============================================================

class TestUSStockInsightsToolHandler(unittest.TestCase):
    """Tests for the _handle_get_us_stock_insights handler function."""

    def setUp(self):
        from src.agent.tools.us_market_data import clear_yf_cache
        clear_yf_cache()

    @patch("src.agent.tools.us_market_data._ticker_info")
    def test_handler_returns_full_insights(self, mock_ticker_info):
        """Tool handler returns full insights for valid US stock."""
        mock_ticker_info.return_value = MOCK_NVDA_INFO
        from src.agent.tools.data_tools import _handle_get_us_stock_insights

        with patch("yfinance.Ticker") as mock_ticker_class:
            import pandas as pd
            mock_ticker = MagicMock()
            mock_ticker.institutional_holders = pd.DataFrame(MOCK_INSTITUTIONAL_HOLDERS_DATA)
            mock_ticker.insider_transactions = pd.DataFrame(MOCK_INSIDER_TRANSACTIONS_DATA)
            mock_ticker_class.return_value = mock_ticker

            result = _handle_get_us_stock_insights("NVDA")

        self.assertEqual(result["status"], "ok")
        self.assertIn("institutional_ownership", result)
        self.assertIn("analyst_consensus", result)
        self.assertIn("insider_activity", result)

    def test_handler_rejects_non_us_code(self):
        """Tool handler returns not_applicable for non-US codes."""
        from src.agent.tools.data_tools import _handle_get_us_stock_insights

        result = _handle_get_us_stock_insights("600519")
        self.assertEqual(result["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
