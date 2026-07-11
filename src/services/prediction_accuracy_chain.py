# -*- coding: utf-8 -*-
"""Daily / weekly prediction accuracy chain.

Reuses DecisionSignal outcomes (1d / 5d) plus optional Deep Research and
analysis_history paper soft-checks. Does not invent a parallel accuracy store.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.config import Config, get_config
from src.services.decision_signal_outcome_service import (
    DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
    DecisionSignalOutcomeService,
    SUPPORTED_OUTCOME_HORIZONS,
)
from src.services.stock_list_parser import split_stock_list
from src.storage import AnalysisHistory, DatabaseManager

logger = logging.getLogger(__name__)

DEFAULT_DAILY_WEEKLY_HORIZONS = ("1d", "5d")
PAPER_SOFT_NEUTRAL_PCT = 2.0


def parse_stock_codes(raw: Optional[str] | Sequence[str]) -> List[str]:
    """Normalize a comma/space separated stock list into unique codes."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = split_stock_list(raw)
    else:
        items = []
        for part in raw:
            items.extend(split_stock_list(str(part)))
    return list(dict.fromkeys(items))


def resolve_stock_codes(
    *,
    stocks: Optional[str] | Sequence[str] = None,
    watchlist: Optional[str] = None,
) -> List[str]:
    """Resolve explicit ``stocks`` or a named watchlist preset (not both empty)."""
    codes = parse_stock_codes(stocks)
    if codes:
        return codes
    if watchlist:
        from src.services.watchlist_presets import load_watchlist_codes

        return load_watchlist_codes(watchlist)
    return []


def parse_horizons(raw: Optional[str] | Sequence[str]) -> List[str]:
    """Parse horizon tokens; default to daily(1d) + weekly(5d)."""
    if raw is None or raw == "" or raw == []:
        return list(DEFAULT_DAILY_WEEKLY_HORIZONS)
    if isinstance(raw, str):
        tokens = [item.strip().lower() for item in raw.replace(";", ",").split(",") if item.strip()]
    else:
        tokens = [str(item).strip().lower() for item in raw if str(item).strip()]
    # aliases
    aliases = {
        "daily": "1d",
        "day": "1d",
        "1": "1d",
        "weekly": "5d",
        "week": "5d",
        "5": "5d",
    }
    normalized: List[str] = []
    for token in tokens:
        horizon = aliases.get(token, token)
        if horizon not in SUPPORTED_OUTCOME_HORIZONS:
            raise ValueError(
                f"unsupported horizon '{token}'; expected one of "
                f"{', '.join(SUPPORTED_OUTCOME_HORIZONS)} or daily/weekly"
            )
        if horizon not in normalized:
            normalized.append(horizon)
    return normalized or list(DEFAULT_DAILY_WEEKLY_HORIZONS)


class PredictionAccuracyChain:
    """Orchestrate predict → (optional research) → outcome recalc → paper check."""

    def __init__(
        self,
        *,
        outcome_service: Optional[DecisionSignalOutcomeService] = None,
        config: Optional[Config] = None,
        db_manager: Optional[DatabaseManager] = None,
    ):
        self.config = config or get_config()
        self.outcome_service = outcome_service or DecisionSignalOutcomeService(db_manager=db_manager)
        self.db = db_manager or DatabaseManager.get_instance()

    def recalc(
        self,
        *,
        stocks: Optional[str] | Sequence[str] = None,
        watchlist: Optional[str] = None,
        horizons: Optional[str] | Sequence[str] = None,
        force: bool = False,
        limit_per_stock: int = 100,
        loop_until_empty: bool = True,
        max_loops: int = 20,
    ) -> Dict[str, Any]:
        """Run DecisionSignal outcome evaluation for daily/weekly horizons."""
        codes = resolve_stock_codes(stocks=stocks, watchlist=watchlist)
        horizons_norm = parse_horizons(horizons)
        per_stock: List[Dict[str, Any]] = []
        totals = {"evaluated": 0, "created": 0, "updated": 0, "skipped": 0}

        targets: Iterable[Optional[str]] = codes if codes else [None]
        for code in targets:
            stock_stats = self._recalc_one_stock(
                stock_code=code,
                horizons=horizons_norm,
                force=force,
                limit_per_stock=limit_per_stock,
                loop_until_empty=loop_until_empty,
                max_loops=max_loops,
            )
            per_stock.append(stock_stats)
            for key in totals:
                totals[key] += int(stock_stats.get(key, 0) or 0)

        stats = self.outcome_service.get_stats(
            horizons=horizons_norm,
            stock_codes=codes or None,
        )
        return {
            "mode": "recalc",
            "stocks": codes,
            "watchlist": watchlist,
            "horizons": horizons_norm,
            "force": bool(force),
            "engine_version": DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
            "totals": totals,
            "per_stock": per_stock,
            "stats": stats,
        }

    def _recalc_one_stock(
        self,
        *,
        stock_code: Optional[str],
        horizons: List[str],
        force: bool,
        limit_per_stock: int,
        loop_until_empty: bool,
        max_loops: int,
    ) -> Dict[str, Any]:
        evaluated = created = updated = skipped = 0
        loops = 0
        while True:
            loops += 1
            result = self.outcome_service.run_outcomes(
                horizons=horizons,
                force=force,
                stock_code=stock_code,
                limit=limit_per_stock,
            )
            evaluated += int(result.get("evaluated", 0) or 0)
            created += int(result.get("created", 0) or 0)
            updated += int(result.get("updated", 0) or 0)
            skipped += int(result.get("skipped", 0) or 0)
            batch_eval = int(result.get("evaluated", 0) or 0)
            if not loop_until_empty or force:
                break
            if batch_eval <= 0 or loops >= max_loops:
                break
            if batch_eval < limit_per_stock:
                break
        return {
            "stock_code": stock_code or "*",
            "loops": loops,
            "evaluated": evaluated,
            "created": created,
            "updated": updated,
            "skipped": skipped,
        }

    def predict(
        self,
        *,
        stocks: Optional[str] | Sequence[str] = None,
        watchlist: Optional[str] = None,
        research: bool = False,
        research_question: Optional[str] = None,
        full_report: bool = False,
        notify: bool = False,
    ) -> Dict[str, Any]:
        """Optional Deep Research, then run standard analysis (writes DecisionSignals)."""
        codes = resolve_stock_codes(stocks=stocks, watchlist=watchlist)
        if not codes:
            raise ValueError("stocks or --watchlist is required for predict")

        research_items: List[Dict[str, Any]] = []
        if research:
            for code in codes:
                research_items.append(
                    self.run_auto_research(
                        stock_code=code,
                        question=research_question,
                    )
                )

        from src.enums import ReportType
        from src.services.analyzer_service import analyze_stock
        from src.notification import NotificationService

        notifier = NotificationService(self.config) if notify else None
        # Focus daily push: SIMPLE/BRIEF → concise card with trend + sources
        report_type = ReportType.FULL if full_report else ReportType.SIMPLE
        analysis_items: List[Dict[str, Any]] = []
        for code in codes:
            try:
                result = analyze_stock(
                    code,
                    config=self.config,
                    full_report=full_report,
                    notifier=notifier,
                    report_type=report_type,
                )
                if result is None:
                    analysis_items.append({"stock_code": code, "success": False, "error": "empty_result"})
                    continue
                analysis_items.append(
                    {
                        "stock_code": code,
                        "success": True,
                        "name": getattr(result, "stock_name", None) or getattr(result, "name", None),
                        "operation_advice": getattr(result, "operation_advice", None),
                        "trend_prediction": getattr(result, "trend_prediction", None),
                        "sentiment_score": getattr(result, "sentiment_score", None),
                        "data_sources": getattr(result, "data_sources", None),
                        "search_performed": getattr(result, "search_performed", None),
                        "action": getattr(result, "action", None),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive per-stock isolation
                logger.exception("predict analysis failed for %s", code)
                analysis_items.append({"stock_code": code, "success": False, "error": str(exc)})

        return {
            "mode": "predict",
            "stocks": codes,
            "watchlist": watchlist,
            "research_enabled": bool(research),
            "research_items": research_items,
            "analysis_items": analysis_items,
            "success_count": sum(1 for item in analysis_items if item.get("success")),
            "failed_count": sum(1 for item in analysis_items if not item.get("success")),
            "note": (
                "DecisionSignals are written by analysis. "
                "Run recalc after 1/5 trading days to score daily/weekly accuracy."
            ),
        }

    def daily(
        self,
        *,
        stocks: Optional[str] | Sequence[str] = None,
        watchlist: Optional[str] = None,
        research: bool = True,
        research_question: Optional[str] = None,
        notify: bool = True,
        horizons: Optional[str] | Sequence[str] = None,
        paper_window: str = "daily",
        skip_recalc: bool = False,
        skip_paper: bool = False,
    ) -> Dict[str, Any]:
        """Daily focus loop: accuracy recalc → Auto Research + predict push → paper check.

        Default watchlist when omitted: ``us_ai_focus,hk_ai_focus``.
        """
        effective_watchlist = watchlist
        if not stocks and not effective_watchlist:
            effective_watchlist = "us_ai_focus,hk_ai_focus"

        recalc_payload: Optional[Dict[str, Any]] = None
        if not skip_recalc:
            recalc_payload = self.recalc(
                stocks=stocks,
                watchlist=effective_watchlist,
                horizons=horizons or "1d,5d",
            )

        predict_payload = self.predict(
            stocks=stocks,
            watchlist=effective_watchlist,
            research=research,
            research_question=research_question,
            full_report=False,
            notify=notify,
        )

        paper_payload: Optional[Dict[str, Any]] = None
        if not skip_paper:
            paper_payload = self.paper_check(
                stocks=stocks,
                watchlist=effective_watchlist,
                window=paper_window,
            )

        return {
            "mode": "daily",
            "watchlist": effective_watchlist,
            "stocks": resolve_stock_codes(stocks=stocks, watchlist=effective_watchlist),
            "research_enabled": bool(research),
            "notify": bool(notify),
            "recalc": recalc_payload,
            "predict": predict_payload,
            "paper": paper_payload,
        }

    def run_auto_research(
        self,
        *,
        stock_code: str,
        question: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run Deep ResearchAgent for one stock (Auto Research)."""
        from src.agent.factory import get_tool_registry
        from src.agent.llm_adapter import LLMToolAdapter
        from src.agent.research import ResearchAgent

        code = str(stock_code or "").strip()
        if not code:
            raise ValueError("stock_code is required")

        default_q = (
            f"Comprehensive prediction research on {code}: "
            "near-term (1-5 trading day) direction, catalysts, risks, and actionable bias."
        )
        query = question.strip() if question and question.strip() else default_q
        query = f"[Stock: {code}] {query}"

        budget = getattr(self.config, "agent_deep_research_budget", 30000)
        timeout = getattr(self.config, "agent_deep_research_timeout", 180)
        agent = ResearchAgent(
            tool_registry=get_tool_registry(),
            llm_adapter=LLMToolAdapter(self.config),
            token_budget=budget,
        )
        result = agent.research(
            query,
            {"stock_code": code, "stock_name": ""},
            timeout_seconds=timeout,
        )
        return {
            "stock_code": code,
            "success": bool(getattr(result, "success", False)),
            "timed_out": bool(getattr(result, "timed_out", False)),
            "findings_count": int(getattr(result, "findings_count", 0) or 0),
            "total_tokens": int(getattr(result, "total_tokens", 0) or 0),
            "duration_s": getattr(result, "duration_s", None),
            "error": getattr(result, "error", None),
            "report": getattr(result, "report", "") or "",
            "sub_questions": list(getattr(result, "sub_questions", []) or []),
        }

    def paper_check(
        self,
        *,
        stocks: Optional[str] | Sequence[str] = None,
        watchlist: Optional[str] = None,
        window: str = "weekly",
    ) -> Dict[str, Any]:
        """Soft-fit latest analysis trend_prediction vs subsequent price move.

        Complements DecisionSignal outcomes for watch/hold (non-directional) rows.
        """
        codes = resolve_stock_codes(stocks=stocks, watchlist=watchlist)
        window_norm = str(window or "weekly").strip().lower()
        if window_norm in {"daily", "1d", "day"}:
            horizon_days = 1
            window_label = "daily"
        elif window_norm in {"weekly", "5d", "week"}:
            horizon_days = 5
            window_label = "weekly"
        else:
            raise ValueError("window must be daily or weekly")

        rows: List[Dict[str, Any]] = []
        with self.db.get_session() as session:
            query = session.query(AnalysisHistory).order_by(AnalysisHistory.created_at.desc())
            if codes:
                query = query.filter(AnalysisHistory.code.in_(codes))
            histories = query.limit(max(50, len(codes) * 5 if codes else 50)).all()

            seen = set()
            for history in histories:
                code = str(history.code or "").strip()
                if not code or code in seen:
                    continue
                if codes and code not in codes:
                    continue
                seen.add(code)
                created = history.created_at.date() if history.created_at else date.today()
                anchor = created
                forward = self.outcome_service.stock_repo.get_forward_bars(
                    code=code,
                    analysis_date=anchor,
                    eval_window_days=horizon_days,
                )
                start_bar = self.outcome_service.stock_repo.get_daily_on_date(
                    code=code,
                    target_date=anchor,
                )
                start_price = float(start_bar.close) if start_bar and start_bar.close is not None else None
                end_close = None
                if forward:
                    last = forward[-1]
                    end_close = float(last.close) if last.close is not None else None
                ret_pct = None
                if start_price and end_close and start_price != 0:
                    ret_pct = round((end_close / start_price - 1.0) * 100.0, 2)

                trend = str(history.trend_prediction or "")
                fit_label, fit_ok = soft_fit_trend(trend, ret_pct)
                rows.append(
                    {
                        "stock_code": code,
                        "name": history.name,
                        "anchor_date": anchor.isoformat(),
                        "window": window_label,
                        "horizon_trading_days": horizon_days,
                        "trend_prediction": trend,
                        "operation_advice": history.operation_advice,
                        "sentiment_score": history.sentiment_score,
                        "return_pct": ret_pct,
                        "soft_fit": fit_label,
                        "soft_ok": fit_ok,
                        "forward_bars": len(forward),
                    }
                )

        scored = [row for row in rows if row.get("soft_ok") is not None]
        hits = sum(1 for row in scored if row.get("soft_ok"))
        return {
            "mode": "paper_check",
            "window": window_label,
            "horizon_trading_days": horizon_days,
            "stocks": codes,
            "watchlist": watchlist,
            "rows": rows,
            "soft_fit_hits": hits,
            "soft_fit_n": len(scored),
            "soft_fit_pct": round(100.0 * hits / len(scored), 1) if scored else None,
        }


def soft_fit_trend(trend: str, return_pct: Optional[float]) -> tuple[str, Optional[bool]]:
    """Ad-hoc soft match used by paper checks (aligned with prior Mag7 rules)."""
    if return_pct is None:
        return "insufficient_price", None
    t = trend or ""
    bullish = ("多" in t and "空" not in t) or ("偏多" in t) or ("震荡偏多" in t)
    bearish = ("空" in t) or ("偏空" in t)
    if "震荡偏多" in t:
        bullish, bearish = True, False
    if t.strip() == "震荡":
        bullish = bearish = False
        range_bound = True
    else:
        range_bound = ("震" in t) and not bullish and not bearish

    if bullish and return_pct > 1:
        return "偏多吻合", True
    if bullish and return_pct < -1:
        return "偏多背离", False
    if bearish and return_pct < -1:
        return "偏空吻合", True
    if bearish and return_pct > 1:
        return "偏空背离", False
    if range_bound and abs(return_pct) < 3:
        return "震荡吻合", True
    if range_bound and return_pct > 3:
        return "震荡偏保守", False
    if range_bound and return_pct < -3:
        return "震荡偏乐观", False
    if (bullish or bearish) and abs(return_pct) <= 1:
        return "近似横盘(弱吻合)", True
    if abs(return_pct) <= PAPER_SOFT_NEUTRAL_PCT:
        return "中性带", None
    return "待观察", None
