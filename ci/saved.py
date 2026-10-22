#!/usr/bin/env python3
"""Saves the list of stocks the user starred in the app (docs/data/saved.json).

Run by the cloud job in mode 'saved' (started from the app whenever the list changes): no packages needed, so it takes
under a minute. The daily scan reads this file and analyses these stocks every day, picked or not.
Usage: python3 ci/saved.py [TICKER ...]   (no tickers = the list was emptied)"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX = 60


def main(argv: list[str], out: Path = Path("docs/data/saved.json")) -> int:
    seen, tickers = set(), []
    for a in argv:
        t = a.strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", t) and t not in seen:
            seen.add(t)
            tickers.append(t)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tickers": tickers[:MAX]}
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"saved {len(data['tickers'])} stocks: {' '.join(data['tickers'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
