# -*- coding: utf-8 -*-
"""Tests for named watchlist presets."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.watchlist_presets import (
    DEFAULT_WATCHLIST_NAME,
    apply_watchlist_to_env_file,
    list_watchlists,
    load_watchlist_codes,
    watchlist_as_stock_list,
)
from src.services.prediction_accuracy_chain import resolve_stock_codes


def test_ai_focus_preset_loads_expected_themes():
    assert DEFAULT_WATCHLIST_NAME in list_watchlists()
    codes = load_watchlist_codes("ai_focus")
    # Mag7 + themes should be present
    for code in ("AAPL", "NVDA", "AMD", "MU", "LITE", "RKLB", "GEV", "hk00700", "000660", "688268"):
        assert code in codes
    # gambling / gold short from old ad-hoc list should not be default focus
    assert "DKNG" not in codes
    assert "GLL" not in codes
    assert len(codes) >= 25
    serialized = watchlist_as_stock_list("ai_focus")
    assert serialized.startswith("AAPL,")
    assert "NVDA" in serialized


def test_resolve_stock_codes_prefers_explicit_stocks():
    assert resolve_stock_codes(stocks="NVDA,AMD", watchlist="ai_focus") == ["NVDA", "AMD"]
    assert "META" in resolve_stock_codes(watchlist="ai_focus")


def test_apply_watchlist_updates_env(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=secret\nSTOCK_LIST=OLD\nDEBUG=false\n", encoding="utf-8")
    result = apply_watchlist_to_env_file("ai_focus", env_path=env_path)
    text = env_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=secret" in text
    assert text.count("STOCK_LIST=") == 1
    assert "NVDA" in text
    assert "OLD" not in text
    assert result["count"] == len(load_watchlist_codes("ai_focus"))


def test_missing_watchlist_raises():
    with pytest.raises(FileNotFoundError):
        load_watchlist_codes("does_not_exist")
