"""What the radar takes from the public Telegram channels, and the quick manual pull.

Every read of the channels (inside the daily scan, or alone with `run.py telegram`) is logged in the journal: when
it ran, how many posts each channel gave, and every stock mention (a post seen again is not counted twice). The app
reads telegram.json: the state of each channel, the stocks mentioned this week, every stock mentioned since the log
started, and the list of reads. The manual pull reads only the channels (2 to 4 minutes in the cloud instead of a
full scan) and updates the mentions next to the stocks of the last scan."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .journal import Journal
from .sources import telegram

log = logging.getLogger("radar.tgwatch")


def channels(settings) -> list[str]:
    return [telegram.channel_name(c) for c in settings.get("telegram.channels") or []]


def enabled(settings) -> bool:
    return bool(settings.get("telegram.enabled", True) and settings.get("telegram.channels"))


def read(src, settings) -> tuple[list[dict], str]:
    """The posts of the last week from all channels, and when they were read (UTC)."""
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    posts = src.telegram_posts(settings.get("telegram.channels"), int(settings.get("telegram.lookback_days") or 7),
                               int(settings.get("telegram.max_pages") or 12))
    return posts, at


def candidate_names(posts: list[dict], results: list[dict], meta: dict | None = None, src=None) -> dict[str, str]:
    """Stocks to look for: the scanned ones, the Hebrew-name dictionary and tickers written as $TICKER / (TICKER)
    (those only when they are listed companies)."""
    known = {t: (u or {}).get("name") or t for t, u in (meta or {}).items()}
    cand = {r["ticker"]: r.get("name") or r["ticker"] for r in results}
    wanted = (set(telegram.HEB_NAMES) | telegram.explicit_tickers(posts)) - set(cand)
    lookup = {}
    if any(t not in known for t in wanted) and src is not None and hasattr(src, "cik_lookup"):
        try:
            lookup = {t: (u or {}).get("name") or t for t, u in (src.cik_lookup() or {}).items()}
        except Exception as e:  # noqa: BLE001
            log.debug("cik lookup failed: %s", e)
    for t in wanted:
        if t in known or t in lookup:
            cand[t] = known.get(t) or lookup.get(t)
        elif t in telegram.HEB_NAMES:          # a well-known name even when the SEC list was not available
            cand[t] = t
    return cand


def summary(settings, posts: list[dict], results: list[dict] | None = None, src=None, meta: dict | None = None,
            read_at: str | None = None, mode: str = "scan") -> tuple[dict, list[dict]]:
    """What the scan data carries about the channels, and every mention found (for the journal)."""
    names = channels(settings)
    per = {c: 0 for c in names}
    for p in posts:
        per[p.get("channel")] = per.get(p.get("channel"), 0) + 1
    out = {"labels": settings.get("telegram.labels") or {}, "channels": names, "per": per, "posts": len(posts),
           "read_at": read_at, "mode": mode, "stats": telegram.channel_stats(posts, names), "stocks": []}
    found: list[dict] = []
    if not posts:
        return out, found
    try:
        results = results or []
        where = {r["ticker"]: i for i, r in enumerate(results)}
        full = telegram.week_overview(posts, candidate_names(posts, results, meta, src), None)
        for x in full:
            found += [{**m, "ticker": x["ticker"], "name": x["name"]} for m in x.pop("all")]
            i = where.get(x["ticker"])
            x["i"] = i                                      # position in the scan results (opens the stock screen)
            x["pick"] = bool(i is not None and results[i].get("pick"))
        out["stocks"] = full[: int(settings.get("telegram.overview_max") or 40)]
    except Exception as e:  # noqa: BLE001
        log.warning("סיכום הטלגרם נכשל: %s", e)
    return out, found


def feed(journal: Journal, summ: dict) -> dict:
    """telegram.json: everything the app's Telegram screen shows."""
    hist = journal.tg_stocks()
    return {"schema": 1, "updated": summ.get("read_at"), "mode": summ.get("mode"),
            "labels": summ.get("labels"), "channels": summ.get("channels"), "per": summ.get("per"),
            "posts": summ.get("posts"), "stats": summ.get("stats"), "week": summ.get("stocks"),
            "reads": journal.tg_reads(30), "history": hist}


def record(journal: Journal, summ: dict, found: list[dict]) -> dict:
    """Log this read and return telegram.json."""
    new = journal.tg_record(summ.get("read_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            summ.get("mode") or "scan", summ.get("per") or {}, found)
    log.info("טלגרם: %d הודעות, %d אזכורים של מניות (%d חדשים ביומן)", summ.get("posts") or 0, len(found), new)
    return feed(journal, summ)


def _write(path: Path, data) -> None:
    from .export import _write as write
    write(path, data)


def _patch(path: Path, posts: list[dict], summ: dict) -> bool:
    """Update the mentions next to the stocks of a saved scan (latest.json / history/<day>.json)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for r in data.get("results") or []:
        got = telegram.mentions(posts, r["ticker"], r.get("name")) if posts else []
        r["tg"], r["tg_n"] = got[:3], (len(got) if posts else None)
    data["telegram"] = summ
    _write(path, data)
    return True


def pull(settings, out_dir: Path | None, demo: bool = False) -> str:
    """The manual pull: read the channels now, log what was taken and update the app data."""
    from .sources import get_sources

    if not enabled(settings):
        raise RuntimeError("קריאת הטלגרם כבויה בקובץ config.yaml (telegram.enabled).")
    src = get_sources(settings, demo)
    posts, at = read(src, settings)
    base = Path(out_dir) / "demo" if out_dir and demo else (Path(out_dir) if out_dir else None)
    latest = None
    if base is not None:
        try:
            latest = json.loads((base / "latest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            latest = None
    results = (latest or {}).get("results") or []
    summ, found = summary(settings, posts, results, src, None, at, "manual")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    journal = Journal(settings.data_dir / ("journal_demo.sqlite" if demo else "journal.sqlite"))
    try:
        data = record(journal, summ, found)
    finally:
        journal.close()
    if base is not None:
        _write(base / "telegram.json", data)
        if latest is not None:
            _patch(base / "latest.json", posts, summ)
            if latest.get("date"):
                hist = base / "history" / f"{latest['date']}.json"
                if hist.exists():
                    _patch(hist, posts, summ)
    labels = summ["labels"]
    parts = ", ".join(f"{labels.get(c, c)} {n}" for c, n in summ["per"].items())
    if not posts:
        raise RuntimeError(f"לא נקראה אף הודעה מערוצי הטלגרם ({parts}). ייתכן שטלגרם חסם זמנית – נסה שוב בעוד שעה.")
    zero = [labels.get(c, c) for c, n in summ["per"].items() if n == 0]
    msg = (f"המשיכה מהטלגרם הסתיימה: {len(posts)} הודעות ({parts}). "
           f"{len(summ['stocks'])} מניות הוזכרו השבוע, {data['history']['n_stocks']} מאז שהתחלנו.")
    if zero:
        msg += f" לא נקראו הודעות מ: {', '.join(zero)}."
    return msg
