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


def test_market_separated_presets_are_mag7_module_tops():
    names = list_watchlists()
    assert DEFAULT_WATCHLIST_NAME in names
    assert "us_ai_focus" in names
    assert "hk_ai_focus" in names

    us = load_watchlist_codes("us_ai_focus")
    hk = load_watchlist_codes("hk_ai_focus")
    combined = load_watchlist_codes("ai_focus")

    # Mag7
    for code in ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"):
        assert code in us
    # Module tops only
    for code in ("AMD", "AVGO", "MU", "LITE", "RKLB"):
        assert code in us
    assert len(us) == 12
    assert all(not code.lower().startswith("hk") for code in us)
    assert all(not is_a_share_code(code) for code in us)
    for code in ("DKNG", "ETHW", "SMCI", "ORCL", "CEG", "INTC", "SNDK"):
        assert code not in us

    # HK internet + innovation
    for code in ("hk00700", "hk09988", "hk03690", "hk01810", "hk09888", "hk00020"):
        assert code in hk
    assert len(hk) == 6
    assert all(code.lower().startswith("hk") for code in hk)

    assert set(us).issubset(set(combined))
    assert set(hk).issubset(set(combined))
    assert set(combined) == set(us) | set(hk)
    for code in ("688268", "000660"):
        assert code not in combined

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


def test_special_attention_preset_matches_screenshot():
    codes = load_watchlist_codes("special_attention")
    expected = [
        "000660",
        "688268",
        "NVDA",
        "SNDK",
        "ETHW",
        "LITE",
        "AMD",
        "GEV",
        "GLL",
        "DKNG",
        "CONL",
    ]
    assert codes == expected
    assert describe_watchlists()["special_attention"]["market"] == "special"


def test_missing_watchlist_raises():
    with pytest.raises(FileNotFoundError):
        load_watchlist_codes("does_not_exist")
