#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply a named watchlist preset to ``.env`` STOCK_LIST (for daily push/analysis).

Examples:
  python scripts/apply_watchlist.py --list
  python scripts/apply_watchlist.py --name ai_focus
  python scripts/apply_watchlist.py --name ai_focus --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply config/watchlists/*.txt to .env STOCK_LIST")
    parser.add_argument("--name", default="ai_focus", help="Preset name (default: ai_focus)")
    parser.add_argument("--list", action="store_true", help="List available presets and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print codes only; do not write .env")
    parser.add_argument("--env-path", default=None, help="Optional .env path (default: repo .env)")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    from src.services.watchlist_presets import (
        apply_watchlist_to_env_file,
        describe_watchlists,
        load_watchlist_codes,
        watchlist_as_stock_list,
    )

    if args.list:
        payload = {"watchlists": describe_watchlists()}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for name, meta in payload["watchlists"].items():
                print(f"{name}: {meta['count']} codes ({meta['path']})")
                print(f"  {','.join(meta['codes'])}")
        return 0

    try:
        codes = load_watchlist_codes(args.name)
        stock_list = watchlist_as_stock_list(args.name)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        payload = {"watchlist": args.name, "count": len(codes), "stock_list": stock_list, "dry_run": True}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"{args.name} ({len(codes)}): {stock_list}")
        return 0

    env_path = Path(args.env_path) if args.env_path else None
    result = apply_watchlist_to_env_file(args.name, env_path=env_path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"Applied watchlist '{result['watchlist']}' -> {result['env_path']} "
            f"({result['count']} codes; replaced={result['replaced']})"
        )
        print(result["stock_list"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
