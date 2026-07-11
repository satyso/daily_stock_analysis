# -*- coding: utf-8 -*-
"""Tests for named watchlist presets."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.watchlist_presets import (
    DEFAULT_WATCHLIST_NAME,
    apply_watchlist_to_env_file,
    describe_watchlists,
    is_a_share_code,
    list_watchlists,
    load_watchlist_codes,
    parse_watchlist_names,
    watchlist_as_stock_list,
)
from src.services.prediction_accuracy_chain import resolve_stock_codes


def test_market_separated_presets_exist_and_exclude_a_shares():
    names = list_watchlists()
    assert DEFAULT_WATCHLIST_NAME in names
    assert "us_ai_focus" in names
    assert "hk_ai_focus" in names

    us = load_watchlist_codes("us_ai_focus")
    hk = load_watchlist_codes("hk_ai_focus")
    combined = load_watchlist_codes("ai_focus")

    # US professional themes
    for code in ("AAPL", "NVDA", "AMD", "MU", "ASML", "LITE", "ANET", "VRT", "CEG", "ORCL", "RKLB"):
        assert code in us
    assert all(not code.lower().startswith("hk") for code in us)
    assert all(not is_a_share_code(code) for code in us)
    # speculative / gambling names stay out of the default US card
    for code in ("DKNG", "ETHW", "GLL", "CONL", "SNDK", "INTC"):
        assert code not in us

    # HK special list only
    for code in ("hk00700", "hk09988", "hk00981", "hk09888", "hk00020"):
        assert code in hk
    assert all(code.lower().startswith("hk") for code in hk)
    assert all(not is_a_share_code(code) for code in hk)

    # Combined = US ∪ HK, still no A-shares / KR-looking bare digits
    assert set(us).issubset(set(combined))
    assert set(hk).issubset(set(combined))
    for code in ("688268", "000660"):
        assert code not in combined
    assert all(not is_a_share_code(code) for code in combined)

    meta = describe_watchlists()
    assert meta["us_ai_focus"]["market"] == "us"
    assert meta["hk_ai_focus"]["market"] == "hk"
    assert meta["ai_focus"]["market"] == "us+hk"


def test_union_watchlist_names():
    assert parse_watchlist_names("us_ai_focus, hk_ai_focus") == ["us_ai_focus", "hk_ai_focus"]
    union = load_watchlist_codes("us_ai_focus,hk_ai_focus")
    assert "NVDA" in union
    assert "hk00700" in union
    assert set(union) == set(load_watchlist_codes("ai_focus"))


def test_resolve_stock_codes_prefers_explicit_stocks():
    assert resolve_stock_codes(stocks="NVDA,AMD", watchlist="ai_focus") == ["NVDA", "AMD"]
    assert "META" in resolve_stock_codes(watchlist="us_ai_focus")
    assert "hk00700" in resolve_stock_codes(watchlist="hk_ai_focus")


def test_apply_watchlist_updates_env(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=secret\nSTOCK_LIST=OLD\nDEBUG=false\n", encoding="utf-8")
    result = apply_watchlist_to_env_file("us_ai_focus", env_path=env_path)
    text = env_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=secret" in text
    assert text.count("STOCK_LIST=") == 1
    assert "NVDA" in text
    assert "hk00700" not in text
    assert "OLD" not in text
    assert result["count"] == len(load_watchlist_codes("us_ai_focus"))
    serialized = watchlist_as_stock_list("us_ai_focus")
    assert serialized.startswith("AAPL,")


def test_missing_watchlist_raises():
    with pytest.raises(FileNotFoundError):
        load_watchlist_codes("does_not_exist")
