"""Optional enrichment from Finnhub (free key at finnhub.io): company news for the last week."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from ..net import Http

log = logging.getLogger("radar.finnhub")

BASE = "https://finnhub.io/api/v1"


def company_news(http: Http, api_key: str, symbol: str, days: int = 7) -> list[dict]:
    if not api_key:
        return []
    end = date.today()
    params = {"symbol": symbol, "from": (end - timedelta(days=days)).isoformat(), "to": end.isoformat(), "token": api_key}
    try:
        data = http.get(f"{BASE}/company-news", params=params, as_json=True, ttl=6 * 3600)
    except Exception as e:  # noqa: BLE001
        log.debug("finnhub news failed %s: %s", symbol, e)
        return []
    out = []
    for it in data or []:
        ts = it.get("datetime")
        out.append({
            "title": it.get("headline", ""),
            "summary": it.get("summary", ""),
            "published": datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None,
            "source": it.get("source", ""),
            "url": it.get("url", ""),
        })
    return [o for o in out if o["title"]]
