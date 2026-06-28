# -*- coding: utf-8 -*-
"""
YFinance-based US stock enrichment data fetcher.

Provides institutional ownership, short interest, analyst consensus,
insider transactions, and sector/industry information for US stocks.
All data comes from YFinance's free tier — no additional API keys needed.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_yf_cache: Dict[str, tuple[float, dict]] = {}
_yf_cache_lock = Lock()
_YF_CACHE_TTL = 300  # 5 minutes


def _ticker_info(stock_code: str) -> Optional[dict]:
    """Fetch YFinance ticker info with in-process cache."""
    code = str(stock_code or "").strip().upper()
    if not code:
        return None

    now = datetime.now().timestamp()
    with _yf_cache_lock:
        cached = _yf_cache.get(code)
        if cached is not None:
            ts, info = cached
            if now - ts < _YF_CACHE_TTL:
                return info

    try:
        import yfinance as yf

        ticker = yf.Ticker(code)
        info = ticker.info
        if not info or info.get("regularMarketPrice") is None and info.get("previousClose") is None:
            logger.debug("YFinance info empty for %s (possibly delisted or invalid ticker)", code)
            return None
        result = dict(info)
        with _yf_cache_lock:
            _yf_cache[code] = (now, result)
        return result
    except Exception as exc:
        logger.warning("YFinance info fetch failed for %s: %s", code, exc)
        return None


def _safe_float(value: Any) -> Optional[float]:
    """Safely coerce a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        v = float(value)
        return v if v == v else None  # NaN check
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    """Safely coerce a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pct(value: Optional[float]) -> Optional[float]:
    """Convert decimal to percentage, rounding to 2 places."""
    if value is None:
        return None
    return round(value * 100, 2)


def get_institutional_ownership(stock_code: str) -> Dict[str, Any]:
    """Get institutional ownership data for a US stock.

    Returns:
        dict with keys:
        - status: 'ok' | 'unavailable'
        - held_pct_institutions: % held by institutions (e.g. 70.87)
        - held_pct_insiders: % held by insiders
        - top_holders: list of top 5 institutional holders [{name, shares, pct_out, date_reported}]
    """
    info = _ticker_info(stock_code)
    if info is None:
        return {"status": "unavailable", "error": "YFinance info not available"}

    inst_pct = _pct(_safe_float(info.get("heldPercentInstitutions")))
    insider_pct = _pct(_safe_float(info.get("heldPercentInsiders")))

    top_holders: List[Dict[str, Any]] = []
    try:
        import yfinance as yf
        ticker = yf.Ticker(str(stock_code).strip().upper())
        holders_df = ticker.institutional_holders
        if holders_df is not None and not holders_df.empty:
            for _, row in holders_df.head(5).iterrows():
                holder = {
                    "name": str(row.get("Holder", "")),
                    "shares": _safe_int(row.get("Shares")),
                    "pct_out": _pct(_safe_float(row.get("pctOut"))),
                    "date_reported": str(row.get("Date Reported", "")),
                }
                top_holders.append(holder)
    except Exception as exc:
        logger.debug("Institutional holders fetch failed for %s: %s", stock_code, exc)

    return {
        "status": "ok",
        "held_pct_institutions": inst_pct,
        "held_pct_insiders": insider_pct,
        "top_holders": top_holders,
    }


def get_short_interest(stock_code: str) -> Dict[str, Any]:
    """Get short interest data for a US stock.

    Returns:
        dict with keys:
        - status: 'ok' | 'unavailable'
        - short_pct_float: % of float shorted
        - short_ratio: days to cover
        - short_ratio_interpretation: human-readable signal
    """
    info = _ticker_info(stock_code)
    if info is None:
        return {"status": "unavailable", "error": "YFinance info not available"}

    short_pct = _pct(_safe_float(info.get("shortPercentOfFloat")))
    short_ratio = _safe_float(info.get("shortRatio"))

    interpretation = "unavailable"
    if short_pct is not None:
        if short_pct < 2:
            interpretation = "low — minimal bearish pressure"
        elif short_pct < 5:
            interpretation = "moderate — some bearish sentiment"
        elif short_pct < 10:
            interpretation = "elevated — notable bearish positioning, potential short squeeze risk"
        elif short_pct < 20:
            interpretation = "high — heavy short interest, high short squeeze potential"
        else:
            interpretation = "extreme — very heavy shorting, major squeeze candidate"

    return {
        "status": "ok",
        "short_pct_float": short_pct,
        "short_ratio_days": short_ratio,
        "interpretation": interpretation,
    }


def get_analyst_consensus(stock_code: str) -> Dict[str, Any]:
    """Get analyst rating consensus for a US stock.

    Returns:
        dict with keys:
        - status: 'ok' | 'unavailable'
        - recommendation_mean: 1.0=Strong Buy, 5.0=Strong Sell
        - recommendation_label: human-readable label
        - number_of_analysts: count of analysts covering
        - target_price: {low, mean, high, current}
    """
    info = _ticker_info(stock_code)
    if info is None:
        return {"status": "unavailable", "error": "YFinance info not available"}

    rating = _safe_float(info.get("recommendationMean"))
    analysts = _safe_int(info.get("numberOfAnalystOpinions"))

    label = "unknown"
    if rating is not None:
        if rating <= 1.5:
            label = "Strong Buy"
        elif rating <= 2.0:
            label = "Buy"
        elif rating <= 3.0:
            label = "Hold"
        elif rating <= 4.0:
            label = "Sell"
        else:
            label = "Strong Sell"

    target = {
        "low": _safe_float(info.get("targetLowPrice")),
        "mean": _safe_float(info.get("targetMeanPrice")),
        "high": _safe_float(info.get("targetHighPrice")),
        "current": _safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
    }

    # Calculate upside if both mean target and current price available
    upside_pct = None
    if target["mean"] and target["current"] and target["current"] > 0:
        upside_pct = round((target["mean"] / target["current"] - 1) * 100, 2)

    return {
        "status": "ok",
        "recommendation_mean": rating,
        "recommendation_label": label,
        "number_of_analysts": analysts,
        "target_price": target,
        "upside_pct": upside_pct,
    }


def get_insider_activity(stock_code: str) -> Dict[str, Any]:
    """Get recent insider transactions for a US stock.

    Returns:
        dict with keys:
        - status: 'ok' | 'unavailable'
        - recent_transactions: list of up to 5 recent insider trades
        - net_signal: 'net_buying' | 'net_selling' | 'neutral' | 'no_data'
        - summary: human-readable summary
    """
    info = _ticker_info(stock_code)
    if info is None:
        return {"status": "unavailable", "error": "YFinance info not available"}

    transactions: List[Dict[str, Any]] = []
    try:
        import yfinance as yf
        ticker = yf.Ticker(str(stock_code).strip().upper())
        insider_df = ticker.insider_transactions
        if insider_df is not None and not insider_df.empty:
            for _, row in insider_df.head(5).iterrows():
                shares = _safe_int(row.get("Shares"))
                txn_type = str(row.get("Transaction", "")).strip()
                # Normalize transaction types
                if not txn_type:
                    txn_type = "Sale" if shares and shares < 0 else "Purchase"
                transactions.append({
                    "insider": str(row.get("Insider", "")),
                    "title": str(row.get("Title", "")),
                    "transaction": txn_type,
                    "shares": shares,
                    "value": _safe_float(row.get("Value")),
                    "date": str(row.get("Date", "")),
                })
    except Exception as exc:
        logger.debug("Insider transactions fetch failed for %s: %s", stock_code, exc)

    net_signal = "no_data"
    if transactions:
        total_shares = sum(t.get("shares", 0) or 0 for t in transactions)
        if total_shares > 0:
            net_signal = "net_buying"
        elif total_shares < 0:
            net_signal = "net_selling"
        else:
            net_signal = "neutral"

    summary = "No recent insider transactions."
    if net_signal == "net_buying":
        summary = f"Insiders are net BUYERS — {len(transactions)} recent transaction(s) show insider accumulation."
    elif net_signal == "net_selling":
        summary = f"Insiders are net SELLERS — {len(transactions)} recent transaction(s) show insider distribution."
    elif net_signal == "neutral":
        summary = f"Mixed insider activity — {len(transactions)} recent transaction(s) with no clear direction."

    return {
        "status": "ok",
        "recent_transactions": transactions,
        "net_signal": net_signal,
        "summary": summary,
    }


def get_sector_info(stock_code: str) -> Dict[str, Any]:
    """Get sector and industry classification for a US stock.

    Returns:
        dict with keys:
        - status: 'ok' | 'unavailable'
        - sector: e.g. 'Technology'
        - industry: e.g. 'Semiconductors'
        - market_cap: market capitalization
        - beta: volatility vs market
    """
    info = _ticker_info(stock_code)
    if info is None:
        return {"status": "unavailable", "error": "YFinance info not available"}

    return {
        "status": "ok",
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _safe_float(info.get("marketCap")),
        "beta": _safe_float(info.get("beta")),
        "fifty_day_avg": _safe_float(info.get("fiftyDayAverage")),
        "two_hundred_day_avg": _safe_float(info.get("twoHundredDayAverage")),
    }


def get_us_stock_insights(stock_code: str) -> Dict[str, Any]:
    """Aggregate all US stock enrichment data into a single response.

    This is the main entry point for Agent tools. Returns institutional ownership,
    short interest, analyst consensus, insider activity, and sector info in one call.

    Args:
        stock_code: US stock ticker (e.g. 'NVDA', 'AAPL')

    Returns:
        dict with keys: status, code, institutional_ownership, short_interest,
        analyst_consensus, insider_activity, sector_info
    """
    code = str(stock_code or "").strip().upper()
    if not code:
        return {"status": "error", "error": "Empty stock code"}

    from data_provider.us_index_mapping import is_us_stock_code

    if not is_us_stock_code(code):
        return {
            "status": "not_applicable",
            "note": "US stock insights are only available for US-listed stocks.",
            "code": code,
        }

    info = _ticker_info(code)
    if info is None:
        return {
            "status": "unavailable",
            "error": f"YFinance returned no data for {code}. The ticker may be invalid or delisted.",
            "code": code,
        }

    return {
        "status": "ok",
        "code": code,
        "name": info.get("shortName") or info.get("longName"),
        "institutional_ownership": get_institutional_ownership(code),
        "short_interest": get_short_interest(code),
        "analyst_consensus": get_analyst_consensus(code),
        "insider_activity": get_insider_activity(code),
        "sector_info": get_sector_info(code),
    }


def clear_yf_cache() -> None:
    """Clear the YFinance in-process cache (for testing)."""
    global _yf_cache
    with _yf_cache_lock:
        _yf_cache.clear()
