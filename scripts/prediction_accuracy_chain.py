#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI for daily/weekly prediction accuracy chain + optional Auto Research.

Examples:
  # Analyze watchlist (writes DecisionSignals); enable Deep Research
  python scripts/prediction_accuracy_chain.py predict \\
    --stocks ETHW,NVDA,DKNG --research

  # Recalculate daily(1d) + weekly(5d) DecisionSignal outcomes
  python scripts/prediction_accuracy_chain.py recalc \\
    --stocks ETHW,NVDA,DKNG --horizons 1d,5d

  # Paper soft-check from analysis_history (covers watch/hold trends)
  python scripts/prediction_accuracy_chain.py paper \\
    --stocks ETHW,NVDA,DKNG --window weekly
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import setup_env

setup_env()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Daily/weekly prediction accuracy chain (DecisionSignal 1d/5d + Auto Research)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    predict = sub.add_parser("predict", help="Optional Auto Research, then analyze stocks")
    predict.add_argument("--stocks", required=True, help="Comma-separated stock codes")
    predict.add_argument(
        "--research",
        action="store_true",
        help="Run Deep ResearchAgent before each analysis (Auto Research)",
    )
    predict.add_argument("--research-question", default=None, help="Optional research focus question")
    predict.add_argument("--full-report", action="store_true", help="Generate full analysis report")
    predict.add_argument("--notify", action="store_true", help="Send notifications after analysis")

    recalc = sub.add_parser("recalc", help="Recalculate DecisionSignal outcomes for 1d/5d")
    recalc.add_argument("--stocks", default=None, help="Optional stock filter; omit for all candidates")
    recalc.add_argument(
        "--horizons",
        default="1d,5d",
        help="Horizons: 1d,5d or aliases daily,weekly (default: 1d,5d)",
    )
    recalc.add_argument("--force", action="store_true", help="Force overwrite completed outcomes")
    recalc.add_argument("--limit", type=int, default=100, help="Candidate limit per stock batch")
    recalc.add_argument(
        "--no-loop",
        action="store_true",
        help="Do not continue batching when evaluated == limit",
    )

    paper = sub.add_parser("paper", help="Paper soft-fit from analysis_history trends")
    paper.add_argument("--stocks", default=None, help="Optional stock filter")
    paper.add_argument(
        "--window",
        default="weekly",
        choices=["daily", "weekly", "1d", "5d"],
        help="Soft-check window (default: weekly)",
    )

    research = sub.add_parser("research", help="Auto Research only (no analysis write)")
    research.add_argument("--stocks", required=True, help="Comma-separated stock codes")
    research.add_argument("--research-question", default=None, help="Optional research focus question")

    for p in (predict, recalc, paper, research):
        p.add_argument(
            "--json",
            action="store_true",
            help="Print machine-readable JSON only",
        )
    return parser


def _print_result(payload: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    mode = payload.get("mode")
    if mode == "recalc":
        totals = payload.get("totals") or {}
        stats = payload.get("stats") or {}
        print("=== Prediction accuracy recalc ===")
        print(f"stocks={payload.get('stocks') or '*'} horizons={payload.get('horizons')}")
        print(
            "evaluated={evaluated} created={created} updated={updated} skipped={skipped}".format(
                **{k: totals.get(k, 0) for k in ("evaluated", "created", "updated", "skipped")}
            )
        )
        print(
            "hit_rate_pct={hit} completed={completed} unable={unable}".format(
                hit=stats.get("hit_rate_pct"),
                completed=stats.get("completed_count", stats.get("completed")),
                unable=stats.get("unable_count", stats.get("unable")),
            )
        )
        return

    if mode == "predict":
        print("=== Predict (+ optional Auto Research) ===")
        print(
            f"success={payload.get('success_count')} failed={payload.get('failed_count')} "
            f"research={payload.get('research_enabled')}"
        )
        for item in payload.get("analysis_items") or []:
            if item.get("success"):
                print(
                    f"- {item.get('stock_code')}: {item.get('operation_advice')} | "
                    f"score={item.get('sentiment_score')} | {item.get('trend_prediction')}"
                )
            else:
                print(f"- {item.get('stock_code')}: FAILED {item.get('error')}")
        print(payload.get("note") or "")
        return

    if mode == "paper_check":
        print("=== Paper soft-check ===")
        print(
            f"window={payload.get('window')} soft_fit="
            f"{payload.get('soft_fit_hits')}/{payload.get('soft_fit_n')} "
            f"({payload.get('soft_fit_pct')}%)"
        )
        for row in payload.get("rows") or []:
            print(
                f"- {row.get('stock_code')}: trend={row.get('trend_prediction')} "
                f"ret={row.get('return_pct')} fit={row.get('soft_fit')}"
            )
        return

    if mode == "research":
        print("=== Auto Research ===")
        for item in payload.get("items") or []:
            status = "OK" if item.get("success") else ("TIMEOUT" if item.get("timed_out") else "FAIL")
            print(
                f"- {item.get('stock_code')}: {status} "
                f"findings={item.get('findings_count')} tokens={item.get('total_tokens')}"
            )
            report = (item.get("report") or "").strip()
            if report:
                preview = report if len(report) <= 1200 else report[:1200] + "\n... (truncated)"
                print(preview)
                print("-" * 40)
        return

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    from src.services.prediction_accuracy_chain import PredictionAccuracyChain

    chain = PredictionAccuracyChain()
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "predict":
            payload = chain.predict(
                stocks=args.stocks,
                research=bool(args.research),
                research_question=args.research_question,
                full_report=bool(args.full_report),
                notify=bool(args.notify),
            )
        elif args.command == "recalc":
            payload = chain.recalc(
                stocks=args.stocks,
                horizons=args.horizons,
                force=bool(args.force),
                limit_per_stock=int(args.limit),
                loop_until_empty=not bool(args.no_loop),
            )
        elif args.command == "paper":
            payload = chain.paper_check(stocks=args.stocks, window=args.window)
        elif args.command == "research":
            from src.services.prediction_accuracy_chain import parse_stock_codes

            items = [
                chain.run_auto_research(
                    stock_code=code,
                    question=args.research_question,
                )
                for code in parse_stock_codes(args.stocks)
            ]
            payload = {"mode": "research", "items": items}
        else:  # pragma: no cover
            parser.error(f"unknown command: {args.command}")
            return 2
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    _print_result(payload, as_json=as_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
