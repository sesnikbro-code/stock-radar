#!/usr/bin/env python3
"""Saves the list of stocks the user starred in the app (docs/data/saved.json).

Run by the cloud job in mode 'saved' (started from the app whenever the list changes): no packages needed, so it takes
under a minute. The daily scan reads this file and analyses these stocks every day, picked or not.

For every newly starred stock it also keeps a snapshot of how the radar saw it then (from the latest scan, or from
its own analysis in the search tab), so the saved-stocks tab can compare 'when you saved it' with 'now' even on a new
phone. A stock that stays in the list keeps its first snapshot.
Usage: python3 ci/saved.py [TICKER ...]   (no tickers = the list was emptied)"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX = 60


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshot(r: dict, date: str | None, spy: list | None) -> dict:
    """The numbers the app shows for a stock, as they were on `date`."""
    plan = r.get("plan") or {}
    spy_last = next((v for v in reversed(spy or []) if v is not None), None)
    return {"date": date, "price": r.get("price"), "upside": r.get("upside"), "risk": r.get("risk"),
            "stop": plan.get("stop"), "target": plan.get("target"), "pick": bool(r.get("pick")),
            "why_not": r.get("why_not"), "pros": (r.get("pros") or [])[:3], "cons": (r.get("cons") or [])[:2],
            "tg_n": r.get("tg_n"), "prob": r.get("prob"), "earnings_date": r.get("earnings_date"), "spy": spy_last,
            "name": r.get("name")}


def find_snapshot(t: str, data_dir: Path) -> dict | None:
    latest = _read(data_dir / "latest.json") or {}
    for r in latest.get("results") or []:
        if r.get("ticker") == t:
            return snapshot(r, latest.get("date"), latest.get("spy_closes"))
    f = _read(data_dir / "tickers" / f"{t}.json")
    if f and f.get("result"):
        return snapshot(f["result"], f.get("date"), f.get("spy_closes") or latest.get("spy_closes"))
    return None


def main(argv: list[str], out: Path = Path("docs/data/saved.json")) -> int:
    seen, tickers = set(), []
    for a in argv:
        t = a.strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", t) and t not in seen:
            seen.add(t)
            tickers.append(t)
    tickers = tickers[:MAX]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = (_read(out) or {}).get("items") or {}
    items = {}
    for t in tickers:
        prev = old.get(t) or {}
        items[t] = prev if prev.get("snap") else {"since": prev.get("since") or now, "snap": find_snapshot(t, out.parent)}
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"updated": now, "tickers": tickers, "items": items}
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"saved {len(tickers)} stocks: {' '.join(tickers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
