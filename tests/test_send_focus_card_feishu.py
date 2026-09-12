# -*- coding: utf-8 -*-
"""Regression tests for Feishu focus-card send fallback."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

sender = importlib.import_module("send_focus_card_feishu")


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        if not self._payload:
            raise ValueError("not json")
        return self._payload


def test_auto_mode_falls_back_to_markdown_when_public_hosts_fail(tmp_path, monkeypatch):
    image = tmp_path / "focus_card_full_20260911.png"
    markdown = tmp_path / "focus_card_full_20260911.md"
    image.write_bytes(b"png")
    markdown.write_text("# 宋总特别关注\n超威半导体 +1.01%\n", encoding="utf-8")

    posted = []

    def fake_post(url, **kwargs):
        posted.append({"url": url, "kwargs": kwargs})
        if "litterbox" in url:
            return _FakeResponse(412, "<html>412 Precondition Failed</html>")
        if "0x0.st" in url:
            return _FakeResponse(
                503,
                "uploads disabled because it’s been almost nothing but AI botnet spam",
            )
        return _FakeResponse(200, payload={"code": 0})

    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)
    monkeypatch.setattr(sender.requests, "post", fake_post)

    rc = sender.main(
        [
            "--mode",
            "auto",
            "--image",
            str(image),
            "--md",
            str(markdown),
            "--title",
            "宋总特别关注",
        ]
    )

    assert rc == 0
    webhook_calls = [item for item in posted if "feishu.cn" in item["url"]]
    assert webhook_calls, posted
    card = webhook_calls[0]["kwargs"]["json"]
    assert card["msg_type"] == "interactive"
    assert "超威半导体" in card["card"]["elements"][0]["content"]


def test_image_mode_falls_back_to_markdown_when_hosts_fail(tmp_path, monkeypatch):
    image = tmp_path / "focus_card_full_20260911.png"
    markdown = tmp_path / "focus_card_full_20260911.md"
    image.write_bytes(b"png")
    markdown.write_text("# 宋总特别关注\n闪迪 +0.93%\n", encoding="utf-8")

    def fake_post(url, **kwargs):
        if "litterbox" in url or "0x0.st" in url:
            return _FakeResponse(503, "uploads disabled")
        return _FakeResponse(200, payload={"code": 0})

    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)
    monkeypatch.setattr(sender.requests, "post", fake_post)

    rc = sender.main(
        [
            "--mode",
            "image",
            "--image",
            str(image),
            "--md",
            str(markdown),
            "--title",
            "宋总特别关注",
        ]
    )

    assert rc == 0


def test_image_mode_without_markdown_still_fails(tmp_path, monkeypatch):
    image = tmp_path / "focus_card_full_20260911.png"
    image.write_bytes(b"png")

    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)
    monkeypatch.setattr(
        sender,
        "_host_image_public",
        lambda _path: None,
    )
    monkeypatch.setattr(sender, "_send_webhook_image_native", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(sender, "_resolve_markdown", lambda _path: None)

    rc = sender.main(["--mode", "image", "--image", str(image)])
    assert rc == 1
