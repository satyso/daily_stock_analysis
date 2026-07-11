# -*- coding: utf-8 -*-
"""Named watchlist presets under ``config/watchlists/``.

Presets are plain text files: ``#`` comments, codes separated by commas/whitespace.
They feed ``STOCK_LIST`` (via apply script) and the prediction accuracy chain.

Market-separated presets:
- ``us_ai_focus`` — US smart-tech / AI chain
- ``hk_ai_focus`` — 港股专项 (HK leaders)
- ``ai_focus`` — US ∪ HK combined (no A-shares); kept for backward compatibility

``--watchlist`` / ``--name`` also accept comma-separated preset names to union codes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.services.stock_list_parser import serialize_stock_list, split_stock_list

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WATCHLIST_DIR = REPO_ROOT / "config" / "watchlists"
DEFAULT_WATCHLIST_NAME = "ai_focus"

# Display / tooling metadata (not required for file discovery).
WATCHLIST_MARKETS: Dict[str, str] = {
    "us_ai_focus": "us",
    "hk_ai_focus": "hk",
    "ai_focus": "us+hk",
    "special_attention": "special",
}

DEFAULT_DAILY_WATCHLIST = "special_attention"

# Pure 6-digit CN A/B share pattern (exclude from US/HK smart presets).
_A_SHARE_CODE_RE = re.compile(r"^\d{6}$")
_HK_PREFIX_RE = re.compile(r"^hk\d{1,5}$", re.IGNORECASE)


def watchlist_dir() -> Path:
    return WATCHLIST_DIR


def list_watchlists() -> List[str]:
    """Return preset names (file stems) sorted alphabetically."""
    if not WATCHLIST_DIR.is_dir():
        return []
    return sorted(path.stem for path in WATCHLIST_DIR.glob("*.txt") if path.is_file())


def parse_watchlist_names(raw: Optional[str] | Sequence[str]) -> List[str]:
    """Parse one or more preset names (comma/whitespace separated)."""
    if raw is None or raw == "" or raw == []:
        return []
    if isinstance(raw, str):
        tokens = [item.strip() for item in re.split(r"[,;\s]+", raw) if item.strip()]
    else:
        tokens = []
        for part in raw:
            tokens.extend(parse_watchlist_names(str(part)))
    # drop .txt suffix; preserve order / uniqueness
    names: List[str] = []
    for token in tokens:
        stem = token[:-4] if token.lower().endswith(".txt") else token
        if stem and stem not in names:
            names.append(stem)
    return names


def resolve_watchlist_path(name: str) -> Path:
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("watchlist name is required")
    # allow "ai_focus" or "ai_focus.txt"
    stem = raw[:-4] if raw.lower().endswith(".txt") else raw
    if "/" in stem or "\\" in stem or stem in {".", ".."}:
        raise ValueError(f"invalid watchlist name: {name}")
    path = WATCHLIST_DIR / f"{stem}.txt"
    if not path.is_file():
        available = ", ".join(list_watchlists()) or "(none)"
        raise FileNotFoundError(f"watchlist '{stem}' not found under {WATCHLIST_DIR}; available: {available}")
    return path


def _load_single_watchlist_codes(name: str) -> List[str]:
    path = resolve_watchlist_path(name)
    codes: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # allow inline trailing comments
        if "#" in stripped:
            stripped = stripped.split("#", 1)[0].strip()
        codes.extend(split_stock_list(stripped))
    return list(dict.fromkeys(codes))


def load_watchlist_codes(name: str = DEFAULT_WATCHLIST_NAME) -> List[str]:
    """Load unique stock codes from one or more named preset files.

    ``name`` may be a single preset (``us_ai_focus``) or a union
    (``us_ai_focus,hk_ai_focus``).
    """
    names = parse_watchlist_names(name)
    if not names:
        raise ValueError("watchlist name is required")
    codes: List[str] = []
    for item in names:
        codes.extend(_load_single_watchlist_codes(item))
    return list(dict.fromkeys(codes))


def watchlist_as_stock_list(name: str = DEFAULT_WATCHLIST_NAME) -> str:
    """Return preset codes in canonical ``STOCK_LIST`` comma-separated form."""
    return serialize_stock_list(",".join(load_watchlist_codes(name)))


def is_a_share_code(code: str) -> bool:
    """Return True for bare 6-digit CN A/B share codes (not HK-prefixed)."""
    text = str(code or "").strip()
    if not text or _HK_PREFIX_RE.match(text):
        return False
    return bool(_A_SHARE_CODE_RE.match(text))


def describe_watchlists() -> Dict[str, Dict[str, object]]:
    """Return name -> {path, count, codes, market} for all presets."""
    result: Dict[str, Dict[str, object]] = {}
    for name in list_watchlists():
        codes = _load_single_watchlist_codes(name)
        result[name] = {
            "name": name,
            "path": str(resolve_watchlist_path(name).relative_to(REPO_ROOT)),
            "count": len(codes),
            "codes": codes,
            "market": WATCHLIST_MARKETS.get(name, "mixed"),
        }
    return result


def apply_watchlist_to_env_file(
    name: str = DEFAULT_WATCHLIST_NAME,
    *,
    env_path: Optional[Path] = None,
) -> Dict[str, object]:
    """Upsert ``STOCK_LIST`` in a ``.env`` file from the named preset(s).

    Does not print or log secret values from other keys.
    """
    target = env_path or (REPO_ROOT / ".env")
    stock_list = watchlist_as_stock_list(name)
    codes = split_stock_list(stock_list)
    if not codes:
        raise ValueError(f"watchlist '{name}' is empty")

    lines: List[str]
    if target.is_file():
        lines = target.read_text(encoding="utf-8").splitlines()
    else:
        lines = [
            "# Generated by scripts/apply_watchlist.py",
            "# Fill remaining secrets from .env.example",
        ]

    replaced = False
    new_lines: List[str] = []
    for line in lines:
        if line.strip().startswith("STOCK_LIST="):
            new_lines.append(f"STOCK_LIST={stock_list}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        # insert after header comments if possible
        insert_at = 0
        for idx, line in enumerate(new_lines):
            if line.strip() and not line.strip().startswith("#"):
                insert_at = idx
                break
            insert_at = idx + 1
        new_lines.insert(insert_at, f"STOCK_LIST={stock_list}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass

    return {
        "watchlist": name,
        "env_path": str(target),
        "count": len(codes),
        "stock_list": stock_list,
        "replaced": replaced,
    }
