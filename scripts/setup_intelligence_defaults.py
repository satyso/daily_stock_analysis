#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bootstrap and enable built-in intelligence sources (Jin10 / 华尔街见闻 / etc.).

Creates missing NewsNow/RSS templates and enables them so international macro
feeds (especially 金十数据) are available for analysis.

Examples:
  python scripts/setup_intelligence_defaults.py
  python scripts/setup_intelligence_defaults.py --fetch
  python scripts/setup_intelligence_defaults.py --json
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

from src.config import setup_env

setup_env()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Enable built-in intelligence sources including Jin10")
    parser.add_argument("--fetch", action="store_true", help="Also pull enabled sources into intelligence_items")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    from src.services.intelligence_service import IntelligenceService

    service = IntelligenceService()
    bootstrap = service.ensure_default_sources_enabled()
    payload = {"bootstrap": bootstrap, "fetch": None}
    if args.fetch:
        # Force refresh path even if NEWS_INTEL_AUTO_FETCH_ENABLED is off
        payload["fetch"] = service.fetch_enabled_sources()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "Intelligence defaults: "
            f"created={bootstrap.get('created_count')} "
            f"enabled={bootstrap.get('enabled_count')} "
            f"errors={bootstrap.get('error_count')} "
            f"total={bootstrap.get('total')}"
        )
        for err in bootstrap.get("errors") or []:
            print(f"  ! {err.get('source')}: {err.get('error')}")
        fetch = payload.get("fetch") or {}
        if fetch:
            print(
                f"Fetch: sources={fetch.get('source_count')} "
                f"saved={fetch.get('saved_count')}"
            )
    return 0 if int(bootstrap.get("error_count") or 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
