#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render focus card: full watch universe + industry views + accuracy.

Card rules:
- stock **names** only (no codes in display)
- tomorrow expected % + range
- per-stock direction accuracy + confidence label
- industry section: view %, key stock, industry accuracy
- universe default: special_attention ∪ us_ai_focus ∪ hk_ai_focus

Examples:
  python scripts/render_focus_card.py
  python scripts/render_focus_card.py --watchlist special_attention,us_ai_focus,hk_ai_focus
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import setup_env

setup_env()

DEFAULT_CARD_WATCHLIST = "special_attention,us_ai_focus,hk_ai_focus"

NAME_MAP = {
    "000660": "SK海力士",
    "688268": "华特气体",
    "NVDA": "英伟达",
    "SNDK": "闪迪",
    "ETHW": "以太坊现货ETF",
    "LITE": "Lumentum",
    "AMD": "超威半导体",
    "GEV": "GE Vernova",
    "GLL": "黄金反向两倍",
    "DKNG": "DraftKings",
    "CONL": "Coinbase两倍做多",
    "AAPL": "苹果",
    "MSFT": "微软",
    "GOOGL": "谷歌",
    "AMZN": "亚马逊",
    "META": "Meta",
    "TSLA": "特斯拉",
    "AVGO": "博通",
    "MU": "美光",
    "RKLB": "Rocket Lab",
    "hk00700": "腾讯",
    "hk09988": "阿里巴巴",
    "hk03690": "美团",
    "hk01810": "小米",
    "hk09888": "百度",
    "hk00020": "商汤",
}

# Industry buckets (display order). A code may appear in one primary industry only.
INDUSTRY_ORDER: List[Tuple[str, List[str]]] = [
    ("存储材料", ["000660", "688268", "SNDK", "MU"]),
    ("算力芯片", ["NVDA", "AMD", "AVGO"]),
    ("光通信", ["LITE"]),
    ("Mag7", ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]),
    ("航天", ["RKLB"]),
    ("电力能源", ["GEV"]),
    ("加密资产", ["ETHW", "CONL"]),
    ("宏观对冲", ["GLL"]),
    ("消费娱乐", ["DKNG"]),
    ("港股互联网", ["hk00700", "hk09988", "hk03690", "hk01810"]),
    ("港股创新", ["hk09888", "hk00020"]),
]

_HK_RE = re.compile(r"^hk0*(\d{1,5})$", re.IGNORECASE)


def _to_yf_symbol(code: str) -> str:
    text = str(code or "").strip()
    upper = text.upper()
    if upper == "000660":
        return "000660.KS"
    if upper == "688268":
        return "688268.SS"
    m = _HK_RE.match(text)
    if m:
        num = m.group(1).zfill(4)
        return f"{num}.HK"
    return upper


def _predict_tomorrow_pct(closes: List[float]) -> Dict[str, float]:
    """Point estimate from recent daily returns (mean shrunk toward 0)."""
    rets = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev:
            rets.append((closes[i] / prev - 1.0) * 100.0)
    recent = rets[-5:] if len(rets) >= 5 else rets
    if not recent:
        return {"pred_pct": 0.0, "low_pct": 0.0, "high_pct": 0.0}
    mean = sum(recent) / len(recent)
    var = sum((x - mean) ** 2 for x in recent) / max(1, len(recent) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    pred = max(-8.0, min(8.0, mean * 0.5))
    band = max(0.4, std * 0.6)
    return {
        "pred_pct": round(pred, 2),
        "low_pct": round(pred - band, 2),
        "high_pct": round(pred + band, 2),
    }


def _direction_hit(pred_pct: float, actual_pct: float, *, flat_eps: float = 0.15) -> bool:
    if abs(pred_pct) < flat_eps and abs(actual_pct) < flat_eps:
        return True
    if pred_pct == 0 or actual_pct == 0:
        return abs(actual_pct) < flat_eps and abs(pred_pct) < flat_eps
    return (pred_pct > 0 and actual_pct > 0) or (pred_pct < 0 and actual_pct < 0)


def _walk_forward_accuracy(closes: List[float], *, window: int = 12) -> Dict[str, Any]:
    if len(closes) < 8:
        return {"acc_pct": None, "hits": 0, "samples": 0, "acc_label": "样本不足", "confidence": "不足"}
    end_i = len(closes) - 1
    start_i = max(5, end_i - window)
    hits = 0
    samples = 0
    for i in range(start_i, end_i):
        pred = _predict_tomorrow_pct(closes[: i + 1])["pred_pct"]
        prev = closes[i]
        if not prev:
            continue
        actual = (closes[i + 1] / prev - 1.0) * 100.0
        samples += 1
        if _direction_hit(float(pred), float(actual)):
            hits += 1
    if samples <= 0:
        return {"acc_pct": None, "hits": 0, "samples": 0, "acc_label": "样本不足", "confidence": "不足"}
    acc = round(100.0 * hits / samples, 1)
    if acc >= 60:
        confidence = "高"
    elif acc >= 45:
        confidence = "中"
    else:
        confidence = "低"
    return {
        "acc_pct": acc,
        "hits": hits,
        "samples": samples,
        "acc_label": f"{acc:.0f}%({hits}/{samples})",
        "confidence": confidence,
    }


def _overall_accuracy(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    hits = sum(int(r.get("hits") or 0) for r in rows if "pred_pct" in r)
    samples = sum(int(r.get("samples") or 0) for r in rows if "pred_pct" in r)
    if samples <= 0:
        return {"acc_pct": None, "hits": 0, "samples": 0, "acc_label": "样本不足"}
    acc = round(100.0 * hits / samples, 1)
    return {
        "acc_pct": acc,
        "hits": hits,
        "samples": samples,
        "acc_label": f"{acc:.0f}%({hits}/{samples})",
    }


def _industry_for_code(code: str) -> str:
    for name, members in INDUSTRY_ORDER:
        if code in members:
            return name
    return "其他"


def build_rows(codes: Sequence[str]) -> List[Dict[str, Any]]:
    import yfinance as yf

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=60)
    rows: List[Dict[str, Any]] = []
    for code in codes:
        name = NAME_MAP.get(code, NAME_MAP.get(code.upper(), code))
        ysym = _to_yf_symbol(code)
        industry = _industry_for_code(code)
        try:
            hist = yf.Ticker(ysym).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat())
            closes = [float(x) for x in hist["Close"].dropna().tolist()] if hist is not None and not hist.empty else []
            if len(closes) < 3:
                rows.append({"name": name, "code": code, "industry": industry, "error": "数据不足"})
                continue
            pred = _predict_tomorrow_pct(closes)
            acc = _walk_forward_accuracy(closes)
            last = closes[-1]
            prev = closes[-2]
            today_pct = round((last / prev - 1.0) * 100.0, 2) if prev else None
            rows.append({
                "name": name,
                "code": code,
                "industry": industry,
                "last": round(last, 4),
                "today_pct": today_pct,
                **pred,
                **acc,
            })
        except Exception as exc:
            rows.append({"name": name, "code": code, "industry": industry, "error": str(exc)})
    return rows


def build_industry_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_code = {r["code"]: r for r in rows if r.get("code")}
    industries: List[Dict[str, Any]] = []
    for industry_name, members in INDUSTRY_ORDER:
        present = [by_code[c] for c in members if c in by_code]
        valid = [r for r in present if "pred_pct" in r]
        if not present:
            continue
        if not valid:
            industries.append({
                "industry": industry_name,
                "pred_pct": None,
                "acc_label": "样本不足",
                "key_name": "—",
                "key_pred": None,
                "count": len(present),
            })
            continue
        pred = round(sum(float(r["pred_pct"]) for r in valid) / len(valid), 2)
        hits = sum(int(r.get("hits") or 0) for r in valid)
        samples = sum(int(r.get("samples") or 0) for r in valid)
        acc_label = (
            f"{round(100.0 * hits / samples):.0f}%({hits}/{samples})" if samples else "样本不足"
        )
        # Key stock: highest |pred| among members; tie-break by accuracy
        key = max(
            valid,
            key=lambda r: (abs(float(r["pred_pct"])), float(r.get("acc_pct") or 0.0)),
        )
        industries.append({
            "industry": industry_name,
            "pred_pct": pred,
            "acc_label": acc_label,
            "key_name": key["name"],
            "key_pred": float(key["pred_pct"]),
            "key_acc": key.get("acc_label", "—"),
            "key_confidence": key.get("confidence", "—"),
            "count": len(present),
        })
    # orphan codes not in INDUSTRY_ORDER
    known = {c for _, members in INDUSTRY_ORDER for c in members}
    orphans = [r for r in rows if r.get("code") and r["code"] not in known and "pred_pct" in r]
    if orphans:
        pred = round(sum(float(r["pred_pct"]) for r in orphans) / len(orphans), 2)
        hits = sum(int(r.get("hits") or 0) for r in orphans)
        samples = sum(int(r.get("samples") or 0) for r in orphans)
        key = max(orphans, key=lambda r: abs(float(r["pred_pct"])))
        industries.append({
            "industry": "其他",
            "pred_pct": pred,
            "acc_label": f"{round(100.0 * hits / samples):.0f}%({hits}/{samples})" if samples else "样本不足",
            "key_name": key["name"],
            "key_pred": float(key["pred_pct"]),
            "key_acc": key.get("acc_label", "—"),
            "key_confidence": key.get("confidence", "—"),
            "count": len(orphans),
        })
    return industries


def _top_up_down(
    rows: List[Dict[str, Any]],
    *,
    limit: int = 5,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    valid = [r for r in rows if "pred_pct" in r]
    ups = sorted(
        [r for r in valid if float(r["pred_pct"]) > 0],
        key=lambda r: float(r["pred_pct"]),
        reverse=True,
    )[:limit]
    downs = sorted(
        [r for r in valid if float(r["pred_pct"]) < 0],
        key=lambda r: float(r["pred_pct"]),
    )[:limit]
    return ups, downs


def format_card_markdown(
    rows: List[Dict[str, Any]],
    industries: List[Dict[str, Any]],
    *,
    title_date: str,
) -> str:
    lines = [
        "# 宋总特别关注",
        title_date,
        "",
        "| 行业 | 明日观点 | 关键股 | 关键股预期 |",
        "|---|---:|---|---:|",
    ]
    for ind in industries:
        pred = ind.get("pred_pct")
        if pred is None:
            pred_s = "暂无"
            key_pred_s = "-"
        else:
            pred_s = f"{pred:+.2f}%"
            kp = ind.get("key_pred")
            key_pred_s = f"{kp:+.2f}%" if kp is not None else "-"
        lines.append(
            f"| {ind['industry']} | {pred_s} | {ind['key_name']} | {key_pred_s} |"
        )

    lines += [
        "",
        "| 股票 | 行业 | 明日预期 |",
        "|---|---|---:|",
    ]
    for row in rows:
        name = row["name"]
        industry = row.get("industry", "-")
        if row.get("error"):
            lines.append(f"| {name} | {industry} | 暂无 |")
            continue
        pred = row["pred_pct"]
        sign = "+" if pred > 0 else ""
        lines.append(f"| {name} | {industry} | {sign}{pred:.2f}% |")

    ups, downs = _top_up_down(rows, limit=5)
    lines += ["", "## Top5 涨"]
    if not ups:
        lines.append("-")
    for i, row in enumerate(ups, 1):
        pred = row["pred_pct"]
        lines.append(f"{i}. {row['name']}  +{pred:.2f}%")
    lines += ["", "## Top5 跌"]
    if not downs:
        lines.append("-")
    for i, row in enumerate(downs, 1):
        pred = row["pred_pct"]
        lines.append(f"{i}. {row['name']}  {pred:.2f}%")
    return "\n".join(lines) + "\n"


def _pct_color(pred: float) -> Tuple[int, int, int]:
    if pred > 0:
        return (52, 211, 153)
    if pred < 0:
        return (251, 113, 133)
    return (226, 232, 240)


def _conf_color(label: str) -> Tuple[int, int, int]:
    if label == "高":
        return (52, 211, 153)
    if label == "中":
        return (251, 191, 36)
    if label == "低":
        return (251, 113, 133)
    return (148, 163, 184)


def render_png_with_pillow(
    rows: List[Dict[str, Any]],
    industries: List[Dict[str, Any]],
    *,
    title_date: str,
    out_png: Path,
) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False

    font_path = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
    try:
        font_title = ImageFont.truetype(font_path, 32)
        font_sub = ImageFont.truetype(font_path, 17)
        font_section = ImageFont.truetype(font_path, 20)
        font_row = ImageFont.truetype(font_path, 17)
        font_small = ImageFont.truetype(font_path, 14)
    except OSError:
        font_title = font_sub = font_section = font_row = font_small = ImageFont.load_default()

    width = 860
    ups, downs = _top_up_down(rows, limit=5)
    height = (
        110
        + 28
        + len(industries) * 28
        + 36
        + 28
        + len(rows) * 26
        + 40
        + max(1, len(ups)) * 26
        + 36
        + max(1, len(downs)) * 26
        + 48
    )
    img = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (18, 18, width - 18, height - 18),
        radius=20,
        fill=(20, 30, 48),
        outline=(71, 85, 105),
        width=2,
    )

    # Upper-left title only
    draw.text((40, 36), "宋总特别关注", font=font_title, fill=(248, 250, 252))
    draw.text((40, 78), title_date, font=font_sub, fill=(148, 163, 184))

    y = 112
    draw.text((40, y), "行业", font=font_small, fill=(148, 163, 184))
    draw.text((250, y), "明日观点", font=font_small, fill=(148, 163, 184))
    draw.text((400, y), "关键股", font=font_small, fill=(148, 163, 184))
    draw.text((600, y), "关键股预期", font=font_small, fill=(148, 163, 184))
    y += 22
    draw.line((40, y, width - 40, y), fill=(51, 65, 85), width=1)
    y += 8

    for ind in industries:
        pred = ind.get("pred_pct")
        draw.text((40, y), ind["industry"][:14], font=font_row, fill=(248, 250, 252))
        if pred is None:
            draw.text((250, y), "暂无", font=font_row, fill=(148, 163, 184))
        else:
            draw.text((250, y), f"{pred:+.2f}%", font=font_row, fill=_pct_color(float(pred)))
        draw.text((400, y), str(ind.get("key_name") or "-")[:10], font=font_row, fill=(226, 232, 240))
        kp = ind.get("key_pred")
        if kp is None:
            draw.text((600, y), "-", font=font_row, fill=(148, 163, 184))
        else:
            draw.text((600, y), f"{float(kp):+.2f}%", font=font_row, fill=_pct_color(float(kp)))
        y += 26

    y += 14
    draw.text((40, y), "股票", font=font_small, fill=(148, 163, 184))
    draw.text((260, y), "行业", font=font_small, fill=(148, 163, 184))
    draw.text((560, y), "明日预期", font=font_small, fill=(148, 163, 184))
    y += 22
    draw.line((40, y, width - 40, y), fill=(51, 65, 85), width=1)
    y += 8

    for row in rows:
        draw.text((40, y), row["name"][:10], font=font_row, fill=(248, 250, 252))
        draw.text((260, y), str(row.get("industry") or "-")[:12], font=font_row, fill=(148, 163, 184))
        if row.get("error"):
            draw.text((560, y), "暂无", font=font_row, fill=(148, 163, 184))
        else:
            pred = float(row["pred_pct"])
            sign = "+" if pred > 0 else ""
            draw.text((560, y), f"{sign}{pred:.2f}%", font=font_row, fill=_pct_color(pred))
        y += 24

    y += 12
    draw.text((40, y), "Top5 涨", font=font_section, fill=(52, 211, 153))
    y += 28
    if not ups:
        draw.text((40, y), "-", font=font_row, fill=(148, 163, 184))
        y += 24
    for i, row in enumerate(ups, 1):
        pred = float(row["pred_pct"])
        draw.text((40, y), f"{i}. {row['name']}", font=font_row, fill=(248, 250, 252))
        draw.text((320, y), f"+{pred:.2f}%", font=font_row, fill=_pct_color(pred))
        y += 24

    y += 10
    draw.text((40, y), "Top5 跌", font=font_section, fill=(251, 113, 133))
    y += 28
    if not downs:
        draw.text((40, y), "-", font=font_row, fill=(148, 163, 184))
        y += 24
    for i, row in enumerate(downs, 1):
        pred = float(row["pred_pct"])
        draw.text((40, y), f"{i}. {row['name']}", font=font_row, fill=(248, 250, 252))
        draw.text((320, y), f"{pred:.2f}%", font=font_row, fill=_pct_color(pred))
        y += 24

    img.save(out_png, format="PNG")
    return out_png.is_file()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render full industry focus card image")
    parser.add_argument("--watchlist", default=DEFAULT_CARD_WATCHLIST)
    parser.add_argument("--out-dir", default="/opt/cursor/artifacts")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from src.services.watchlist_presets import load_watchlist_codes

    codes = load_watchlist_codes(args.watchlist)
    rows = build_rows(codes)
    industries = build_industry_rows(rows)
    overall = _overall_accuracy(rows)
    today = datetime.now().strftime("%Y-%m-%d")
    md = format_card_markdown(rows, industries, title_date=today)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"focus_card_full_{today.replace('-', '')}"
    md_path = out_dir / f"{stem}.md"
    png_path = out_dir / f"{stem}.png"
    json_path = out_dir / f"{stem}.json"
    # also refresh the legacy tomorrow stem for continuity
    legacy_png = out_dir / f"focus_card_tomorrow_{today.replace('-', '')}.png"
    legacy_md = out_dir / f"focus_card_tomorrow_{today.replace('-', '')}.md"

    md_path.write_text(md, encoding="utf-8")
    legacy_md.write_text(md, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "date": today,
                "watchlist": args.watchlist,
                "overall_accuracy": overall,
                "industries": industries,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    ok = render_png_with_pillow(rows, industries, title_date=today, out_png=png_path)
    if ok:
        # copy to legacy name so previous links still work
        legacy_png.write_bytes(png_path.read_bytes())

    payload = {
        "markdown": str(md_path),
        "png": str(png_path) if ok else None,
        "legacy_png": str(legacy_png) if ok else None,
        "json": str(json_path),
        "image_ok": ok,
        "count": len(rows),
        "industry_count": len(industries),
        "overall_accuracy": overall,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(md)
        print(f"PNG: {png_path if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
