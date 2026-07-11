#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render special-attention tomorrow % forecast card as Markdown + PNG image.

Card rules (user):
- stock **names** only (no codes)
- predict **tomorrow** expected % move (not just direction)
- no information-source line
- convert via Chrome headless screenshot (API-equivalent image copy)

Examples:
  python scripts/render_focus_card.py
  python scripts/render_focus_card.py --watchlist special_attention --out-dir /opt/cursor/artifacts
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import setup_env

setup_env()

# Display names for the maintained special-attention list
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
}

YF_MAP = {
    "000660": "000660.KS",
    "688268": "688268.SS",
    "NVDA": "NVDA",
    "SNDK": "SNDK",
    "ETHW": "ETHW",
    "LITE": "LITE",
    "AMD": "AMD",
    "GEV": "GEV",
    "GLL": "GLL",
    "DKNG": "DKNG",
    "CONL": "CONL",
}


def _predict_tomorrow_pct(closes: List[float]) -> Dict[str, float]:
    """Point estimate for next-session % move from recent daily returns.

    Uses mean of last 5 daily returns, shrunk toward 0 by half of recent
    volatility so estimates stay conservative (not wild).
    """
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
    # shrink mean toward 0
    pred = mean * 0.5
    # clamp extreme single-day forecasts
    pred = max(-8.0, min(8.0, pred))
    band = max(0.4, std * 0.6)
    return {
        "pred_pct": round(pred, 2),
        "low_pct": round(pred - band, 2),
        "high_pct": round(pred + band, 2),
    }


def _direction_hit(pred_pct: float, actual_pct: float, *, flat_eps: float = 0.15) -> bool:
    """Direction hit: same sign, or both near-flat."""
    if abs(pred_pct) < flat_eps and abs(actual_pct) < flat_eps:
        return True
    if pred_pct == 0 or actual_pct == 0:
        return abs(actual_pct) < flat_eps and abs(pred_pct) < flat_eps
    return (pred_pct > 0 and actual_pct > 0) or (pred_pct < 0 and actual_pct < 0)


def _walk_forward_accuracy(closes: List[float], *, window: int = 12) -> Dict[str, Any]:
    """Backtest the same heuristic over recent sessions (direction accuracy)."""
    if len(closes) < 8:
        return {"acc_pct": None, "hits": 0, "samples": 0, "acc_label": "样本不足"}
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
        return {"acc_pct": None, "hits": 0, "samples": 0, "acc_label": "样本不足"}
    acc = round(100.0 * hits / samples, 1)
    return {
        "acc_pct": acc,
        "hits": hits,
        "samples": samples,
        "acc_label": f"{acc:.0f}%({hits}/{samples})",
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


def build_rows(codes: Sequence[str]) -> List[Dict[str, Any]]:
    import yfinance as yf

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=60)
    rows: List[Dict[str, Any]] = []
    for code in codes:
        name = NAME_MAP.get(code, code)
        ysym = YF_MAP.get(code, code)
        try:
            hist = yf.Ticker(ysym).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat())
            closes = [float(x) for x in hist["Close"].dropna().tolist()] if hist is not None and not hist.empty else []
            if len(closes) < 3:
                rows.append({"name": name, "error": "数据不足"})
                continue
            pred = _predict_tomorrow_pct(closes)
            acc = _walk_forward_accuracy(closes)
            last = closes[-1]
            prev = closes[-2]
            today_pct = round((last / prev - 1.0) * 100.0, 2) if prev else None
            rows.append({
                "name": name,
                "code": code,
                "last": round(last, 4),
                "today_pct": today_pct,
                **pred,
                **acc,
            })
        except Exception as exc:
            rows.append({"name": name, "error": str(exc)})
    return rows


def format_card_markdown(rows: List[Dict[str, Any]], *, title_date: str) -> str:
    overall = _overall_accuracy(rows)
    lines = [
        f"# 特别关注 · 明日预测",
        f"{title_date} · 近端方向准确率 {overall['acc_label']}",
        "",
        "| 股票 | 明日预期 | 区间 | 准确率 |",
        "|---|---:|---:|---:|",
    ]
    for row in rows:
        name = row["name"]
        if row.get("error"):
            lines.append(f"| {name} | 暂无 | — | — |")
            continue
        pred = row["pred_pct"]
        sign = "+" if pred > 0 else ""
        low = row["low_pct"]
        high = row["high_pct"]
        lines.append(
            f"| {name} | {sign}{pred:.2f}% | {low:+.2f}% ~ {high:+.2f}% | {row.get('acc_label', '—')} |"
        )
    lines += [
        "",
        "## Top 5（明日弹性）",
    ]
    scored = sorted(
        [r for r in rows if "pred_pct" in r],
        key=lambda r: abs(float(r["pred_pct"])),
        reverse=True,
    )[:5]
    for i, row in enumerate(scored, 1):
        pred = row["pred_pct"]
        sign = "+" if pred > 0 else ""
        lines.append(
            f"{i}. **{row['name']}**  明日 {sign}{pred:.2f}% "
            f"（{row['low_pct']:+.2f}% ~ {row['high_pct']:+.2f}%）· 准确率 {row.get('acc_label', '—')}"
        )
    lines += [
        "",
        "说明：明日涨跌幅为近端收益收缩估计；准确率为同法近12个交易日方向命中率，供决策参考，不构成投资建议。",
    ]
    return "\n".join(lines) + "\n"


def format_card_html(rows: List[Dict[str, Any]], *, title_date: str) -> str:
    overall = _overall_accuracy(rows)
    body_rows = []
    for row in rows:
        name = html.escape(row["name"])
        if row.get("error"):
            body_rows.append(
                f"<tr><td>{name}</td><td class='muted'>暂无</td>"
                f"<td class='muted'>—</td><td class='muted'>—</td></tr>"
            )
            continue
        pred = float(row["pred_pct"])
        cls = "up" if pred > 0 else ("down" if pred < 0 else "flat")
        sign = "+" if pred > 0 else ""
        body_rows.append(
            "<tr>"
            f"<td class='name'>{name}</td>"
            f"<td class='{cls}'>{sign}{pred:.2f}%</td>"
            f"<td class='range'>{row['low_pct']:+.2f}% ~ {row['high_pct']:+.2f}%</td>"
            f"<td class='acc'>{html.escape(str(row.get('acc_label') or '—'))}</td>"
            "</tr>"
        )
    top = sorted([r for r in rows if "pred_pct" in r], key=lambda r: abs(float(r["pred_pct"])), reverse=True)[:5]
    top_html = []
    for i, row in enumerate(top, 1):
        pred = float(row["pred_pct"])
        cls = "up" if pred > 0 else ("down" if pred < 0 else "flat")
        sign = "+" if pred > 0 else ""
        top_html.append(
            f"<li><span class='name'>{html.escape(row['name'])}</span> "
            f"<span class='{cls}'>{sign}{pred:.2f}%</span> "
            f"<span class='acc'>{html.escape(str(row.get('acc_label') or '—'))}</span></li>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<style>
  body {{ margin:0; font-family: "PingFang SC","Noto Sans CJK SC","Segoe UI",sans-serif;
         background: linear-gradient(160deg,#0f172a 0%,#1e293b 55%,#0b1220 100%); color:#e2e8f0; }}
  .card {{ width: 720px; margin: 24px auto; padding: 28px 28px 22px;
           background: rgba(15,23,42,.92); border: 1px solid rgba(148,163,184,.25);
           border-radius: 18px; box-shadow: 0 18px 50px rgba(0,0,0,.35); }}
  h1 {{ margin:0 0 6px; font-size: 28px; letter-spacing: .02em; }}
  .sub {{ color:#94a3b8; margin-bottom: 18px; font-size: 14px; }}
  table {{ width:100%; border-collapse: collapse; font-size: 15px; }}
  th {{ text-align:left; color:#94a3b8; font-weight:600; padding: 8px 6px; border-bottom:1px solid rgba(148,163,184,.25); }}
  td {{ padding: 10px 6px; border-bottom:1px solid rgba(148,163,184,.12); }}
  .name {{ font-weight: 600; color:#f8fafc; }}
  .up {{ color:#34d399; font-weight:700; }}
  .down {{ color:#fb7185; font-weight:700; }}
  .flat {{ color:#e2e8f0; font-weight:700; }}
  .range,.muted,.acc {{ color:#94a3b8; }}
  h2 {{ margin: 22px 0 10px; font-size: 18px; }}
  ol {{ margin:0; padding-left: 22px; }}
  li {{ margin: 6px 0; }}
  .note {{ margin-top: 18px; color:#64748b; font-size: 12px; }}
</style></head><body>
<div class="card">
  <h1>特别关注 · 明日预测</h1>
  <div class="sub">{html.escape(title_date)} · 近端方向准确率 {html.escape(overall['acc_label'])}</div>
  <table>
    <thead><tr><th>股票</th><th>明日预期</th><th>区间</th><th>准确率</th></tr></thead>
    <tbody>
      {''.join(body_rows)}
    </tbody>
  </table>
  <h2>Top 5</h2>
  <ol>{''.join(top_html)}</ol>
  <div class="note">明日涨跌幅为近端收益收缩估计；准确率为同法近12个交易日方向命中率，仅供参考，不构成投资建议。</div>
</div>
</body></html>
"""


def render_png_with_pillow(rows: List[Dict[str, Any]], *, title_date: str, out_png: Path) -> bool:
    """Draw a clean forecast card image with Pillow (no browser dependency)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False

    font_path = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
    try:
        font_title = ImageFont.truetype(font_path, 34)
        font_sub = ImageFont.truetype(font_path, 18)
        font_row = ImageFont.truetype(font_path, 20)
        font_small = ImageFont.truetype(font_path, 15)
    except OSError:
        font_title = font_sub = font_row = font_small = ImageFont.load_default()

    width = 760
    overall = _overall_accuracy(rows)
    top5 = sorted([r for r in rows if "pred_pct" in r], key=lambda r: abs(float(r["pred_pct"])), reverse=True)[:5]
    height = 170 + len(rows) * 40 + 40 + len(top5) * 32 + 100
    img = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        (24, 24, width - 24, height - 24),
        radius=22,
        fill=(20, 30, 48),
        outline=(71, 85, 105),
        width=2,
    )
    draw.text((48, 44), "特别关注 · 明日预测", font=font_title, fill=(248, 250, 252))
    draw.text(
        (48, 90),
        f"{title_date} · 近端方向准确率 {overall['acc_label']}",
        font=font_sub,
        fill=(148, 163, 184),
    )

    y = 130
    draw.text((48, y), "股票", font=font_small, fill=(148, 163, 184))
    draw.text((280, y), "明日预期", font=font_small, fill=(148, 163, 184))
    draw.text((420, y), "区间", font=font_small, fill=(148, 163, 184))
    draw.text((600, y), "准确率", font=font_small, fill=(148, 163, 184))
    y += 28
    draw.line((48, y, width - 48, y), fill=(51, 65, 85), width=1)
    y += 12

    for row in rows:
        name = row["name"]
        if row.get("error"):
            draw.text((48, y), name, font=font_row, fill=(248, 250, 252))
            draw.text((280, y), "暂无", font=font_row, fill=(148, 163, 184))
            y += 38
            continue
        pred = float(row["pred_pct"])
        color = (52, 211, 153) if pred > 0 else ((251, 113, 133) if pred < 0 else (226, 232, 240))
        sign = "+" if pred > 0 else ""
        draw.text((48, y), name, font=font_row, fill=(248, 250, 252))
        draw.text((280, y), f"{sign}{pred:.2f}%", font=font_row, fill=color)
        draw.text(
            (420, y),
            f"{row['low_pct']:+.2f}% ~ {row['high_pct']:+.2f}%",
            font=font_row,
            fill=(148, 163, 184),
        )
        draw.text((600, y), str(row.get("acc_label") or "—"), font=font_row, fill=(148, 163, 184))
        y += 38

    y += 8
    draw.text((48, y), "Top 5", font=font_sub, fill=(226, 232, 240))
    y += 28
    for i, row in enumerate(top5, 1):
        pred = float(row["pred_pct"])
        color = (52, 211, 153) if pred > 0 else ((251, 113, 133) if pred < 0 else (226, 232, 240))
        sign = "+" if pred > 0 else ""
        draw.text((48, y), f"{i}. {row['name']}", font=font_row, fill=(248, 250, 252))
        draw.text((360, y), f"{sign}{pred:.2f}%", font=font_row, fill=color)
        draw.text((500, y), str(row.get("acc_label") or "—"), font=font_row, fill=(148, 163, 184))
        y += 32

    draw.text(
        (48, height - 70),
        "准确率=同法近12个交易日方向命中；明日%为近端收益收缩估计，仅供参考。",
        font=font_small,
        fill=(100, 116, 139),
    )
    img.save(out_png, format="PNG")
    return out_png.is_file()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render tomorrow % focus card image")
    parser.add_argument("--watchlist", default="special_attention")
    parser.add_argument("--out-dir", default="/opt/cursor/artifacts")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from src.services.watchlist_presets import load_watchlist_codes

    codes = load_watchlist_codes(args.watchlist)
    rows = build_rows(codes)
    overall = _overall_accuracy(rows)
    today = datetime.now().strftime("%Y-%m-%d")
    md = format_card_markdown(rows, title_date=today)
    html_text = format_card_html(rows, title_date=today)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"focus_card_tomorrow_{today.replace('-', '')}"
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    png_path = out_dir / f"{stem}.png"
    json_path = out_dir / f"{stem}.json"
    md_path.write_text(md, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "date": today,
                "watchlist": args.watchlist,
                "overall_accuracy": overall,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    ok = render_png_with_pillow(rows, title_date=today, out_png=png_path)

    payload = {
        "markdown": str(md_path),
        "html": str(html_path),
        "png": str(png_path) if ok else None,
        "json": str(json_path),
        "image_ok": ok,
        "count": len(rows),
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
