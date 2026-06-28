# -*- coding: utf-8 -*-
"""
Portfolio digest: read today's individual stock reports and produce an
LLM-generated consolidated portfolio-level summary.

Usage (CLI):
    python main.py --portfolio-digest

Usage (API):
    POST /api/v1/analysis/portfolio-digest

The digest reads all analysis_history records from today, extracts key
signals (score, advice, trend, summary), and asks the LLM to produce a
concise portfolio overview: which stocks look strongest, which need
attention, cross-cutting themes, and overall risk posture.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

try:
    import litellm
    _HAS_LITELLM = True
except ImportError:
    litellm = None  # type: ignore
    _HAS_LITELLM = False

logger = logging.getLogger(__name__)

_DIGEST_PROMPT_ZH = """你是一名投资组合策略师，正在审阅今日的股票分析结果。

以下是投资组合中 {stock_count} 只股票的个股分析结果。
每只股票包含：代码、名称、情绪评分（0-100）、操作建议、趋势预测、一句话总结。

{analyses}

仅基于以上信息，撰写一份简明的投资组合级别总结，涵盖：

1. **整体组合健康状况** — 综合情绪，多空比例
2. **最强持仓** — 哪些股票表现最好及原因（每只1-2句话）
3. **需要关注的持仓** — 哪些股票出现警示信号（每只1-2句话）
4. **交叉主题** — 板块、宏观或组合间共同模式
5. **建议操作** — 2-3条可执行的投资组合管理建议
6. **风险状况** — 整体风险水平及关键风险因素

请具体、数据驱动、简明。使用分析中的实际评分和建议。
不要编造上面未提供的数据。用中文输出。总回复控制在500字以内。"""


def fetch_todays_reports(
    codes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Fetch today's analysis reports from the database.

    Args:
        codes: Optional list of stock codes to filter. None = all codes from today.

    Returns:
        List of report dicts sorted by created_at.
    """
    from src.storage import get_db

    db = get_db()
    records = db.get_analysis_history(days=1, limit=200)

    today = date.today()
    reports: List[Dict[str, Any]] = []
    seen_codes: set[str] = set()

    for record in records:
        d = record.to_dict()
        created = record.created_at
        if created is None or created.date() != today:
            continue
        rt = d.get("report_type", "")
        if rt not in ("analysis", ""):
            continue

        code = (d.get("code") or "").strip().upper()
        if not code:
            continue
        if code in seen_codes:
            continue
        if codes and code not in {c.upper() for c in codes}:
            continue

        seen_codes.add(code)
        reports.append(d)

    reports.sort(key=lambda r: r.get("created_at", ""))
    return reports


def format_analyses_for_prompt(reports: List[Dict[str, Any]], lang: str = "zh") -> str:
    """Format individual stock analyses into a compact text block for the LLM prompt."""
    lines: List[str] = []
    for i, r in enumerate(reports, 1):
        code = r.get("code", "?").upper()
        name = r.get("name") or code
        score = r.get("sentiment_score")
        advice = r.get("operation_advice") or "未提供"
        trend = r.get("trend_prediction") or "未提供"
        summary = r.get("analysis_summary") or "未提供"

        score_str = f"{score}/100" if score is not None else "未评分"

        lines.append(
            f"#{i} {code} {name}\n"
            f"  评分: {score_str} | 建议: {advice} | 趋势: {trend}\n"
            f"  摘要: {summary}"
        )

    return "\n\n".join(lines)


def _resolve_model() -> str:
    """Resolve the primary LLM model from system config."""
    from src.config import get_config
    config = get_config()
    return (getattr(config, "litellm_model", "") or "").strip()


def generate_portfolio_digest(
    codes: Optional[List[str]] = None,
    lang: str = "zh",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a portfolio-level digest from today's individual stock analyses.

    Args:
        codes: Optional stock codes to include. None = all stocks analyzed today.
        lang: Language for the digest ('zh' or 'en').
        model: Optional model override. None = use system default.

    Returns:
        dict with keys: status, digest_text, stock_count, stocks_included, model_used, error
    """
    # 1. Fetch reports
    try:
        reports = fetch_todays_reports(codes=codes)
    except Exception as exc:
        logger.error("Failed to fetch today's reports: %s", exc)
        return {
            "status": "error",
            "error": f"Failed to fetch reports: {exc}",
            "stock_count": 0,
            "digest_text": None,
        }

    if not reports:
        return {
            "status": "no_data",
            "error": "No analysis reports found for today. Run an analysis first.",
            "stock_count": 0,
            "digest_text": None,
        }

    included_codes = [r.get("code", "?").upper() for r in reports]

    # 2. Resolve model
    resolved_model = model or _resolve_model()
    if not resolved_model:
        return {
            "status": "error",
            "error": "No LLM model configured. Set LITELLM_MODEL in .env.",
            "stock_count": len(reports),
            "digest_text": None,
        }

    # 3. Build prompt
    formatted = format_analyses_for_prompt(reports, lang=lang)
    prompt = _DIGEST_PROMPT_ZH.format(stock_count=len(reports), analyses=formatted)

    # 4. Call LLM
    try:
        if not _HAS_LITELLM:
            raise RuntimeError("litellm not installed")

        response = litellm.completion(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.5,
        )
        digest_text = response.choices[0].message.content
        model_used = getattr(response, "model", resolved_model)

        logger.info(
            "Portfolio digest generated: %d stocks, model=%s, chars=%d",
            len(reports), model_used, len(digest_text) if digest_text else 0,
        )

        return {
            "status": "ok",
            "digest_text": digest_text,
            "stock_count": len(reports),
            "stocks_included": included_codes,
            "model_used": model_used,
        }

    except Exception as exc:
        logger.error("LLM call for portfolio digest failed: %s", exc)
        return {
            "status": "error",
            "error": f"LLM generation failed: {exc}",
            "stock_count": len(reports),
            "digest_text": None,
        }
