#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send the latest Song Zong focus card to Feishu.

Prefer PNG image. Modes:
  1) FEISHU_APP_ID + FEISHU_APP_SECRET: upload image_key, send native image via webhook
  2) webhook-only: upload PNG to a short-lived public host and send a button card link
  3) fallback: markdown / text card

Requires one of:
  - FEISHU_WEBHOOK_URL (+ optional FEISHU_WEBHOOK_SECRET / FEISHU_WEBHOOK_KEYWORD)
  - FEISHU_APP_ID + FEISHU_APP_SECRET + FEISHU_CHAT_ID

Examples:
  python scripts/send_focus_card_feishu.py
  python scripts/send_focus_card_feishu.py --image /opt/cursor/artifacts/focus_card_full_20260717.png
  python scripts/send_focus_card_feishu.py --mode md --md /opt/cursor/artifacts/focus_card_full_20260717.md
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ARTIFACTS = Path("/opt/cursor/artifacts")


def _sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _webhook_ok(status_code: int, body: dict) -> bool:
    # Feishu/Lark may return code=0 / StatusCode=0; treat missing as failure.
    if status_code != 200:
        return False
    if "code" in body:
        return int(body.get("code") or 0) == 0
    if "StatusCode" in body:
        return int(body.get("StatusCode") or 0) == 0
    return False


def _with_sign(payload: dict, secret: str) -> dict:
    if not secret:
        return payload
    ts = str(int(time.time()))
    out = dict(payload)
    out["timestamp"] = ts
    out["sign"] = _sign(secret, ts)
    return out


def _open_api_bases(webhook_url: str) -> list[str]:
    host = (urlparse(webhook_url).hostname or "").lower()
    if "larkoffice.com" in host or "larksuite.com" in host:
        return [
            "https://open.larkoffice.com/open-apis",
            "https://open.feishu.cn/open-apis",
        ]
    return [
        "https://open.feishu.cn/open-apis",
        "https://open.larkoffice.com/open-apis",
    ]


def _tenant_access_token(app_id: str, app_secret: str, bases: list[str]) -> Optional[str]:
    body = {"app_id": app_id, "app_secret": app_secret}
    for base in bases:
        try:
            resp = requests.post(
                f"{base}/auth/v3/tenant_access_token/internal",
                json=body,
                timeout=30,
            )
            data = resp.json()
        except Exception as exc:
            print(f"tenant_token_error base={base} err={exc}")
            continue
        token = (data.get("tenant_access_token") or "").strip()
        if token:
            return token
        print(f"tenant_token_failed base={base} body={data}")
    return None


def _upload_image_key(token: str, image_path: Path, bases: list[str]) -> Optional[str]:
    headers = {"Authorization": f"Bearer {token}"}
    for base in bases:
        try:
            with image_path.open("rb") as fh:
                resp = requests.post(
                    f"{base}/im/v1/images",
                    headers=headers,
                    data={"image_type": "message"},
                    files={"image": (image_path.name, fh, "image/png")},
                    timeout=60,
                )
            data = resp.json()
        except Exception as exc:
            print(f"image_upload_error base={base} err={exc}")
            continue
        key = ((data.get("data") or {}).get("image_key") or "").strip()
        if key:
            print(f"image_key={key} base={base}")
            return key
        print(f"image_upload_failed base={base} body={data}")
    return None


def _host_image_public(image_path: Path) -> Optional[str]:
    """Short-lived public URL fallback when App upload credentials are absent."""
    try:
        with image_path.open("rb") as fh:
            resp = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": (image_path.name, fh, "image/png")},
                timeout=60,
            )
        url = (resp.text or "").strip()
        if url.startswith("http"):
            print(f"public_image_url={url}")
            return url
        print(f"litterbox_failed status={resp.status_code} body={resp.text[:200]}")
    except Exception as exc:
        print(f"litterbox_error={exc}")

    try:
        with image_path.open("rb") as fh:
            resp = requests.post(
                "https://0x0.st",
                files={"file": (image_path.name, fh, "image/png")},
                timeout=60,
            )
        url = (resp.text or "").strip()
        if url.startswith("http"):
            print(f"public_image_url={url}")
            return url
        print(f"zero_failed status={resp.status_code} body={resp.text[:200]}")
    except Exception as exc:
        print(f"zero_error={exc}")
    return None


def _post_webhook(payload: dict) -> tuple[bool, dict[str, Any], int]:
    url = (os.getenv("FEISHU_WEBHOOK_URL") or "").strip()
    secret = (os.getenv("FEISHU_WEBHOOK_SECRET") or "").strip()
    body = _with_sign(payload, secret)
    resp = requests.post(url, json=body, timeout=30)
    data: dict[str, Any] = {}
    try:
        data = resp.json()
    except Exception:
        pass
    return _webhook_ok(resp.status_code, data), data, resp.status_code


def _send_webhook_image_native(image_path: Path, *, title: str) -> bool:
    webhook = (os.getenv("FEISHU_WEBHOOK_URL") or "").strip()
    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    if not (webhook and app_id and app_secret):
        return False
    bases = _open_api_bases(webhook)
    token = _tenant_access_token(app_id, app_secret, bases)
    if not token:
        return False
    image_key = _upload_image_key(token, image_path, bases)
    if not image_key:
        return False
    ok, data, status = _post_webhook(
        {"msg_type": "image", "content": {"image_key": image_key}}
    )
    print(f"webhook_image_status={status} body={data}")
    if ok:
        # Optional short caption after the image.
        _post_webhook(
            {
                "msg_type": "text",
                "content": {"text": f"{title}\n关注卡图片已推送"},
            }
        )
    return ok


def _send_webhook_image_link(image_path: Path, *, title: str) -> bool:
    public_url = _host_image_public(image_path)
    if not public_url:
        return False
    keyword = (os.getenv("FEISHU_WEBHOOK_KEYWORD") or "").strip()
    intro = f"**{title}**\n文字卡片不好读，请点按钮查看完整关注卡图片。"
    if keyword and keyword not in intro:
        intro = f"{keyword}\n{intro}"
    ok, data, status = _post_webhook(
        {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"{title}（图片版）"},
                    "template": "blue",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": intro}},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "打开关注卡图片"},
                                "type": "primary",
                                "url": public_url,
                            }
                        ],
                    },
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": (
                                    "当前为链接预览（约 72 小时有效）。"
                                    "若要群里直接显示图片，请配置 FEISHU_APP_ID/"
                                    "FEISHU_APP_SECRET（开通 im:resource）。"
                                ),
                            }
                        ],
                    },
                ],
            },
        }
    )
    print(f"webhook_image_link_status={status} body={data}")
    return ok


def _send_webhook_markdown(content: str, *, title: str) -> bool:
    keyword = (os.getenv("FEISHU_WEBHOOK_KEYWORD") or "").strip()
    body_text = content
    if keyword and keyword not in body_text:
        body_text = f"{keyword}\n{body_text}"
    ok, data, status = _post_webhook(
        {
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
    )
    if ok:
        print(f"webhook_card_status={status} body={data}")
        return True
    ok2, data2, status2 = _post_webhook(
        {
            "msg_type": "text",
            "content": {"text": f"{title}\n\n{body_text[:7000]}"},
        }
    )
    print(f"webhook_text_status={status2} body={data2}")
    return ok2


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


def _resolve_image(path_arg: str, md_path: Optional[Path]) -> Optional[Path]:
    if path_arg:
        p = Path(path_arg)
        return p if p.is_file() else None
    if md_path is not None:
        sibling = md_path.with_suffix(".png")
        if sibling.is_file():
            return sibling
    candidates = sorted(ARTIFACTS.glob("focus_card_full_*.png"), reverse=True)
    if candidates:
        return candidates[0]
    candidates = sorted(ARTIFACTS.glob("focus_card_tomorrow_*.png"), reverse=True)
    return candidates[0] if candidates else None


def _resolve_markdown(path_arg: str) -> Optional[Path]:
    if path_arg:
        p = Path(path_arg)
        return p if p.is_file() else None
    candidates = sorted(ARTIFACTS.glob("focus_card_full_*.md"), reverse=True)
    if candidates:
        return candidates[0]
    candidates = sorted(ARTIFACTS.glob("focus_card_tomorrow_*.md"), reverse=True)
    return candidates[0] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send focus card to Feishu")
    parser.add_argument("--md", default="", help="Path to focus card markdown")
    parser.add_argument("--image", default="", help="Path to focus card PNG")
    parser.add_argument(
        "--mode",
        choices=("auto", "image", "md"),
        default="auto",
        help="auto: prefer image; image: PNG only; md: markdown/text only",
    )
    parser.add_argument("--title", default="宋总特别关注")
    args = parser.parse_args(argv)

    md_path = _resolve_markdown(args.md)
    image_path = _resolve_image(args.image, md_path)

    has_webhook = bool((os.getenv("FEISHU_WEBHOOK_URL") or "").strip())
    has_app = bool(
        (os.getenv("FEISHU_APP_ID") or "").strip()
        and (os.getenv("FEISHU_APP_SECRET") or "").strip()
        and (os.getenv("FEISHU_CHAT_ID") or "").strip()
    )
    if not has_webhook and not has_app:
        print(
            "ERROR: Feishu not configured. Set FEISHU_WEBHOOK_URL "
            "or FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CHAT_ID.",
            file=sys.stderr,
        )
        return 3

    prefer_image = args.mode in ("auto", "image")
    ok = False

    if prefer_image and image_path is not None and has_webhook:
        ok = _send_webhook_image_native(image_path, title=args.title)
        if not ok:
            ok = _send_webhook_image_link(image_path, title=args.title)
        print(f"image={image_path}")

    if not ok and args.mode != "image":
        if md_path is None:
            print("ERROR: no focus card markdown found", file=sys.stderr)
            return 2
        content = md_path.read_text(encoding="utf-8").strip()
        if not content:
            print("ERROR: empty markdown", file=sys.stderr)
            return 2
        if has_webhook:
            ok = _send_webhook_markdown(content, title=args.title)
        if not ok and has_app:
            ok = _send_via_app(content, title=args.title)
        print(f"markdown={md_path}")

    if not ok and args.mode == "image":
        print("ERROR: image send failed", file=sys.stderr)
        return 1

    print(f"feishu_ok={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
