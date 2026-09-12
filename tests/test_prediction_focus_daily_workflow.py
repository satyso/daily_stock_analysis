# -*- coding: utf-8 -*-
"""Static checks for Focus prediction daily workflow delivery contract."""

from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT_DIR / ".github/workflows/prediction-focus-daily.yml"


def _load_focus_daily_env() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["focus-daily"]["steps"]
    chain_step = next((step for step in steps if step.get("name") == "Run daily focus chain"), None)
    assert chain_step is not None
    return chain_step["env"]


def test_prediction_focus_daily_maps_core_generation_keys() -> None:
    env = _load_focus_daily_env()
    for key in (
        "AIHUBMIX_KEY",
        "LLM_CHANNELS",
        "LITELLM_CONFIG",
        "GEMINI_MODEL",
        "ANTHROPIC_API_KEY",
    ):
        assert key in env


def test_prediction_focus_daily_falls_back_to_markdown_card() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "--mode auto" in text
    assert '--md "$MD"' in text
    assert "Upload focus card artifacts" in text
