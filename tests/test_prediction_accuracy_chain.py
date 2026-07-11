# -*- coding: utf-8 -*-
"""Tests for prediction accuracy chain helpers and recalc orchestration."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.config import Config
from src.services.prediction_accuracy_chain import (
    PredictionAccuracyChain,
    parse_horizons,
    parse_stock_codes,
    soft_fit_trend,
)
from src.storage import AnalysisHistory, DatabaseManager, DecisionSignalRecord, StockDaily


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    db_path = tmp_path / "prediction_accuracy_chain.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def test_parse_stock_codes_and_horizons():
    assert parse_stock_codes("NVDA, AMD；DKNG") == ["NVDA", "AMD", "DKNG"]
    assert parse_horizons(None) == ["1d", "5d"]
    assert parse_horizons("daily,weekly") == ["1d", "5d"]
    assert parse_horizons(["3d", "1d", "1d"]) == ["3d", "1d"]
    with pytest.raises(ValueError):
        parse_horizons("intraday")


def test_soft_fit_trend_rules():
    assert soft_fit_trend("看多", 5.0)[1] is True
    assert soft_fit_trend("看空", 5.0)[1] is False
    assert soft_fit_trend("震荡", 1.0)[1] is True
    assert soft_fit_trend("震荡偏多", 4.0)[1] is True
    assert soft_fit_trend("看多", None)[1] is None


def test_recalc_loops_per_stock(isolated_db):
    outcome = MagicMock()
    outcome.run_outcomes.side_effect = [
        {"evaluated": 2, "created": 2, "updated": 0, "skipped": 0},
        {"evaluated": 0, "created": 0, "updated": 0, "skipped": 1},
    ]
    outcome.get_stats.return_value = {
        "hit_rate_pct": 50.0,
        "completed": 2,
        "unable": 0,
        "hit": 1,
        "miss": 1,
        "neutral": 0,
        "total": 2,
        "engine_version": "decision-signal-v1",
        "horizons": ["1d", "5d"],
        "statuses": ["active"],
        "breakdowns": {},
        "unable_reasons": {},
        "avg_stock_return_pct": None,
        "stock_codes": ["NVDA"],
    }
    chain = PredictionAccuracyChain(outcome_service=outcome, db_manager=isolated_db)
    result = chain.recalc(stocks="NVDA", horizons="1d,5d", limit_per_stock=2, max_loops=5)
    assert result["totals"]["evaluated"] == 2
    assert result["stats"]["hit_rate_pct"] == 50.0
    assert outcome.run_outcomes.call_count == 2
    assert outcome.get_stats.call_args.kwargs["stock_codes"] == ["NVDA"]


def test_get_stats_stock_code_filter(isolated_db):
    from src.services.decision_signal_outcome_service import DecisionSignalOutcomeService
    from src.storage import DecisionSignalOutcomeRecord

    with isolated_db.get_session() as session:
        for code, action in (("NVDA", "buy"), ("AMD", "sell")):
            session.add(
                DecisionSignalRecord(
                    stock_code=code,
                    market="us",
                    action=action,
                    horizon="1d",
                    status="active",
                    source_type="analysis",
                    trigger_source="api",
                    plan_quality="complete",
                    created_at=datetime.utcnow(),
                )
            )
        session.commit()
        signals = session.query(DecisionSignalRecord).all()
        for signal in signals:
            session.add(
                DecisionSignalOutcomeRecord(
                    signal_id=signal.id,
                    horizon="1d",
                    engine_version="decision-signal-v1",
                    action=signal.action,
                    market=signal.market,
                    source_type="analysis",
                    eval_status="completed",
                    outcome="hit" if signal.action == "buy" else "miss",
                    direction_expected="up" if signal.action == "buy" else "not_up",
                    direction_correct=signal.action == "buy",
                    anchor_date=date.today() - timedelta(days=2),
                    eval_window_days=1,
                    start_price=100.0,
                    end_close=105.0 if signal.action == "buy" else 110.0,
                    stock_return_pct=5.0 if signal.action == "buy" else 10.0,
                )
            )
        session.commit()

    service = DecisionSignalOutcomeService(db_manager=isolated_db)
    all_stats = service.get_stats(horizons=["1d"])
    nvda_stats = service.get_stats(horizons=["1d"], stock_code="NVDA")
    assert all_stats["total"] == 2
    assert nvda_stats["total"] == 1
    assert nvda_stats["hit"] == 1
    assert nvda_stats["stock_codes"] is not None


def test_paper_check_uses_history_and_forward_bars(isolated_db):
    anchor = date.today() - timedelta(days=10)
    with isolated_db.get_session() as session:
        session.add(
            AnalysisHistory(
                query_id="q1",
                code="ETHW",
                name="ETHW",
                sentiment_score=59,
                operation_advice="持有观察",
                trend_prediction="看多",
                created_at=datetime.combine(anchor, datetime.min.time()),
            )
        )
        session.add(
            StockDaily(
                code="ETHW",
                date=anchor,
                open=10.0,
                high=11.0,
                low=9.5,
                close=10.0,
                volume=1000,
            )
        )
        for offset, close in enumerate((10.2, 10.5, 10.8, 11.0, 11.2), start=1):
            session.add(
                StockDaily(
                    code="ETHW",
                    date=anchor + timedelta(days=offset),
                    open=close,
                    high=close + 0.2,
                    low=close - 0.2,
                    close=close,
                    volume=1000,
                )
            )
        session.commit()

    chain = PredictionAccuracyChain(db_manager=isolated_db)
    result = chain.paper_check(stocks="ETHW", window="weekly")
    assert result["soft_fit_n"] == 1
    assert result["soft_fit_hits"] == 1
    assert result["rows"][0]["soft_ok"] is True
