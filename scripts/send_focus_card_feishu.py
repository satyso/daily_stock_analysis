#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send the latest Song Zong focus card markdown to Feishu.

Requires one of:
  - FEISHU_WEBHOOK_URL (+ optional FEISHU_WEBHOOK_SECRET / FEISHU_WEBHOOK_KEYWORD)
  - FEISHU_APP_ID + FEISHU_APP_SECRET + FEISHU_CHAT_ID

Examples:
  python scripts/send_focus_card_feishu.py
  python scripts/send_focus_card_feishu.py --md /opt/cursor/artifacts/focus_card_full_20260713.md
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _send_webhook(content: str, *, title: str) -> bool:
    url = (os.getenv("FEISHU_WEBHOOK_URL") or "").strip()
    if not url:
        return False
    keyword = (os.getenv("FEISHU_WEBHOOK_KEYWORD") or "").strip()
    secret = (os.getenv("FEISHU_WEBHOOK_SECRET") or "").strip()
    body_text = content
    if keyword and keyword not in body_text:
        body_text = f"{keyword}\n{body_text}"
    # Prefer interactive card markdown for readability.
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": body_text[:18000]},
            ],
        },
    }
    if secret:
        ts = str(int(time.time()))
        card["timestamp"] = ts
        card["sign"] = _sign(secret, ts)
    resp = requests.post(url, json=card, timeout=30)
    data = {}
    try:
        data = resp.json()
    except Exception:
        pass
    ok = resp.status_code == 200 and int(data.get("code", data.get("StatusCode", 1)) or 1) == 0
    if not ok:
        # Fallback to plain text
        payload = {"msg_type": "text", "content": {"text": f"{title}\n\n{body_text[:7000]}"}}
        if secret:
            ts = str(int(time.time()))
            payload["timestamp"] = ts
            payload["sign"] = _sign(secret, ts)
        resp2 = requests.post(url, json=payload, timeout=30)
        try:
            data2 = resp2.json()
        except Exception:
            data2 = {}
        ok = resp2.status_code == 200 and int(data2.get("code", data2.get("StatusCode", 1)) or 1) == 0
        print(f"webhook_text_status={resp2.status_code} body={data2}")
        return ok
    print(f"webhook_card_status={resp.status_code} body={data}")
    return True


def _send_via_app(content: str, *, title: str) -> bool:
    try:
        from src.config import setup_env

        setup_env()
        from src.config import get_config
        from src.notification_sender.feishu_sender import FeishuSender
    except Exception as exc:
        print(f"app_bot_import_failed={exc}")
        return False
    config = get_config()
    if not (
        getattr(config, "feishu_app_id", None)
        and getattr(config, "feishu_app_secret", None)
        and getattr(config, "feishu_chat_id", None)
    ):
        return False
    sender = FeishuSender(config)
    return bool(sender.send_to_feishu(content, title=title))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send focus card to Feishu")
    parser.add_argument("--md", default="", help="Path to focus card markdown")
    parser.add_argument("--title", default="宋总特别关注")
    args = parser.parse_args(argv)

    md_path = Path(args.md) if args.md else None
    if md_path is None:
        artifacts = Path("/opt/cursor/artifacts")
        candidates = sorted(artifacts.glob("focus_card_full_*.md"), reverse=True)
        if not candidates:
            candidates = sorted(artifacts.glob("focus_card_tomorrow_*.md"), reverse=True)
        if not candidates:
            print("ERROR: no focus card markdown found under /opt/cursor/artifacts", file=sys.stderr)
            return 2
        md_path = candidates[0]

    if not md_path.is_file():
        print(f"ERROR: markdown not found: {md_path}", file=sys.stderr)
        return 2

    content = md_path.read_text(encoding="utf-8").strip()
    if not content:
        print("ERROR: empty markdown", file=sys.stderr)
        return 2

    has_webhook = bool((os.getenv("FEISHU_WEBHOOK_URL") or "").strip())
    has_app = bool(
        (os.getenv("FEISHU_APP_ID") or "").strip()
        and (os.getenv("FEISHU_APP_SECRET") or "").strip()
        and (os.getenv("FEISHU_CHAT_ID") or "").strip()
    )
    if not has_webhook and not has_app:
        print(
            "ERROR: Feishu not configured. Set FEISHU_WEBHOOK_URL "
            "or FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CHAT_ID in the Cloud Agent secrets.",
            file=sys.stderr,
        )
        print(f"markdown={md_path}")
        return 3

    ok = False
    if has_webhook:
        ok = _send_webhook(content, title=args.title)
    if not ok and has_app:
        ok = _send_via_app(content, title=args.title)
    print(f"markdown={md_path}")
    print(f"feishu_ok={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
