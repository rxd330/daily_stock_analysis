# -*- coding: utf-8 -*-
"""
Portfolio digest: read stock reports for a given date and produce an
LLM-generated consolidated portfolio-level summary.

Usage (CLI):
    python main.py --portfolio-digest
    python main.py --portfolio-digest --digest-date 2026-06-27

Usage (API):
    POST /api/v1/analysis/portfolio-digest?date=2026-06-27
    GET  /api/v1/analysis/available-digest-dates
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import litellm
    _HAS_LITELLM = True
except ImportError:
    litellm = None  # type: ignore
    _HAS_LITELLM = False

logger = logging.getLogger(__name__)

_DIGEST_PROMPT_ZH = """你是一名投资组合策略师，正在审阅{date_label}的股票分析结果。

以下是投资组合中 {stock_count} 只股票的个股分析结果。
每只股票包含：代码、名称、情绪评分（0-100）、操作建议、趋势预测、一句话总结。
注意：部分分析可能来自较早日期——每只股票的"分析日期"已标注。

{analyses}

{staleness_note}
仅基于以上信息，撰写一份简明的投资组合级别总结，涵盖：

1. **整体组合健康状况** — 综合情绪，多空比例
2. **最强持仓** — 哪些股票表现最好及原因（每只1-2句话）
3. **需要关注的持仓** — 哪些股票出现警示信号（每只1-2句话）
4. **交叉主题** — 板块、宏观或组合间共同模式
5. **建议操作** — 2-3条可执行的投资组合管理建议
6. **风险状况** — 整体风险水平及关键风险因素

请具体、数据驱动、简明。使用分析中的实际评分和建议。
不要编造上面未提供的数据。用中文输出。总回复控制在500字以内。"""


def _parse_date(raw: Optional[str]) -> Optional[date]:
    """Parse a date string, returning None on failure."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def fetch_reports_for_date(
    target_date: Optional[date] = None,
    codes: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], date]:
    """Fetch the latest analysis report for each stock, then filter to those
    relevant for the target date.

    For freshness: we return ALL stocks that have any report within a
    reasonable window, then compute_freshness marks which ones are from
    the target date vs older.

    Args:
        target_date: Reference date. None = today.
        codes: Optional stock codes to filter.

    Returns:
        (reports, effective_date)
    """
    from src.storage import get_db

    effective_date = target_date or date.today()
    db = get_db()
    records = db.get_analysis_history(days=7, limit=500)

    # Build latest-per-code map
    latest: Dict[str, Dict[str, Any]] = {}
    for record in records:
        created = record.created_at
        if created is None:
            continue
        rt = getattr(record, "report_type", None) or ""
        if rt not in ("analysis", ""):
            continue

        d = record.to_dict()
        code = (d.get("code") or "").strip().upper()
        if not code:
            continue
        if codes and code not in {c.upper() for c in codes}:
            continue

        if code not in latest or created > latest[code].get("_created_dt", datetime.min):
            d["_created_dt"] = created
            latest[code] = d

    # Include stocks whose latest report is within 7 days of target_date
    reports: List[Dict[str, Any]] = []
    cutoff = effective_date
    for code, d in latest.items():
        created_dt = d.pop("_created_dt", None)
        report_date = created_dt.date() if created_dt else effective_date
        # Include if report is from target_date or within 7 days before
        if (cutoff - report_date).days <= 7:
            reports.append(d)

    reports.sort(key=lambda r: r.get("created_at", ""))
    return reports, effective_date


def get_available_dates(days_back: int = 30) -> List[str]:
    """Return all dates that have analysis reports, sorted newest first.

    Args:
        days_back: How many days to look back (default 30).

    Returns:
        List of date strings in YYYY-MM-DD format.
    """
    from src.storage import get_db

    db = get_db()
    records = db.get_analysis_history(days=days_back, limit=2000)

    dates: set[str] = set()
    for record in records:
        created = record.created_at
        if created is None:
            continue
        rt = getattr(record, "report_type", None) or ""
        if rt not in ("analysis", ""):
            continue
        dates.add(created.strftime("%Y-%m-%d"))

    return sorted(dates, reverse=True)


def compute_stock_freshness(
    reports: List[Dict[str, Any]],
    target_date: date,
) -> List[Dict[str, Any]]:
    """Compute freshness info for each stock in the report list.

    Returns list of {code, name, report_date, age_days, fresh}.
    fresh = True if report is from target_date, False otherwise.
    """
    result: List[Dict[str, Any]] = []
    for r in reports:
        code = (r.get("code") or "").upper()
        name = r.get("name") or code
        created_str = r.get("created_at", "")
        report_date = target_date
        age_days = 0

        if created_str:
            try:
                if "T" in str(created_str):
                    report_date = datetime.fromisoformat(str(created_str).replace("Z", "+00:00")).date()
                else:
                    report_date = datetime.strptime(str(created_str)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass

        age_days = (target_date - report_date).days
        fresh = age_days == 0

        result.append({
            "code": code,
            "name": name,
            "report_date": report_date.isoformat(),
            "age_days": age_days,
            "fresh": fresh,
        })

    return result


def format_analyses_for_prompt(
    reports: List[Dict[str, Any]],
    freshness: List[Dict[str, Any]],
    lang: str = "zh",
) -> str:
    """Format individual stock analyses into a compact text block, with freshness annotations."""
    fresh_map = {f["code"]: f for f in freshness}
    lines: List[str] = []

    for i, r in enumerate(reports, 1):
        code = r.get("code", "?").upper()
        name = r.get("name") or code
        score = r.get("sentiment_score")
        advice = r.get("operation_advice") or "未提供"
        trend = r.get("trend_prediction") or "未提供"
        summary = r.get("analysis_summary") or "未提供"
        score_str = f"{score}/100" if score is not None else "未评分"

        f_info = fresh_map.get(code, {})
        report_date = f_info.get("report_date", "")
        age_days = f_info.get("age_days", 0)

        if age_days == 0:
            date_tag = "分析日期: 今日"
        elif age_days == 1:
            date_tag = f"分析日期: 昨日 ({report_date})"
        else:
            date_tag = f"分析日期: {age_days}天前 ({report_date})"

        lines.append(
            f"#{i} {code} {name} [{date_tag}]\n"
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
    target_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a portfolio-level digest from stock analyses for a given date.

    Args:
        codes: Optional stock codes. None = all stocks for that date.
        lang: 'zh' or 'en'.
        model: Optional model override.
        target_date: Date string YYYY-MM-DD. None = today.

    Returns:
        dict with keys:
        - status: 'ok' | 'no_data' | 'error'
        - digest_text: the LLM-generated summary
        - target_date: date the digest covers
        - is_today: whether target_date is today
        - stock_count: number of stocks included
        - stocks_included: list of stock codes
        - stocks_freshness: [{code, name, report_date, age_days, fresh}]
        - any_stale: True if any stock report is >0 days old
        - model_used: LLM model name
        - error: error message if status != 'ok'
    """
    parsed_date = _parse_date(target_date)
    effective_date = parsed_date or date.today()

    # 1. Fetch reports
    try:
        reports, _ = fetch_reports_for_date(target_date=parsed_date, codes=codes)
    except Exception as exc:
        logger.error("Failed to fetch reports for %s: %s", effective_date, exc)
        return {
            "status": "error",
            "error": f"Failed to fetch reports: {exc}",
            "target_date": effective_date.isoformat(),
            "is_today": effective_date == date.today(),
            "stock_count": 0,
            "digest_text": None,
        }

    if not reports:
        return {
            "status": "no_data",
            "error": f"No analysis reports found for {effective_date}. Run an analysis first.",
            "target_date": effective_date.isoformat(),
            "is_today": effective_date == date.today(),
            "stock_count": 0,
            "digest_text": None,
        }

    # 2. Compute freshness
    freshness = compute_stock_freshness(reports, effective_date)
    any_stale = any(not f["fresh"] for f in freshness)
    included_codes = [r.get("code", "?").upper() for r in reports]

    # 3. Resolve model
    resolved_model = model or _resolve_model()
    if not resolved_model:
        return {
            "status": "error",
            "error": "No LLM model configured. Set LITELLM_MODEL in .env.",
            "target_date": effective_date.isoformat(),
            "is_today": effective_date == date.today(),
            "stock_count": len(reports),
            "stocks_freshness": freshness,
            "any_stale": any_stale,
            "digest_text": None,
        }

    # 4. Build prompt
    is_today = effective_date == date.today()
    if is_today:
        date_label = "今日"
    elif (date.today() - effective_date).days == 1:
        date_label = f"昨日（{effective_date}）"
    else:
        date_label = f"{effective_date}"

    staleness_note = ""
    if any_stale:
        stale_codes = [f["code"] for f in freshness if not f["fresh"]]
        staleness_note = (
            f"⚠️ 注意：以下股票的分析数据不是最新的：{', '.join(stale_codes)}。"
            f"在评估这些股票时请降低置信度，并注明数据可能已过时。\n\n"
        )

    formatted = format_analyses_for_prompt(reports, freshness, lang=lang)
    prompt = _DIGEST_PROMPT_ZH.format(
        date_label=date_label,
        stock_count=len(reports),
        analyses=formatted,
        staleness_note=staleness_note,
    )

    # 5. Call LLM
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
            "Portfolio digest generated: %d stocks, date=%s, model=%s, chars=%d, stale=%s",
            len(reports), effective_date, model_used,
            len(digest_text) if digest_text else 0, any_stale,
        )

        return {
            "status": "ok",
            "digest_text": digest_text,
            "target_date": effective_date.isoformat(),
            "is_today": is_today,
            "stock_count": len(reports),
            "stocks_included": included_codes,
            "stocks_freshness": freshness,
            "any_stale": any_stale,
            "model_used": model_used,
        }

    except Exception as exc:
        logger.error("LLM call for portfolio digest failed: %s", exc)
        return {
            "status": "error",
            "error": f"LLM generation failed: {exc}",
            "target_date": effective_date.isoformat(),
            "is_today": is_today,
            "stock_count": len(reports),
            "stocks_freshness": freshness,
            "any_stale": any_stale,
            "digest_text": None,
        }
