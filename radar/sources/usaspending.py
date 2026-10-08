"""US federal contract awards (USASpending.gov, free, no key)."""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from ..net import Http

log = logging.getLogger("radar.usaspending")

URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|holdings?|group|ltd|limited|plc|llc|lp|sa|nv|ag|"
    r"technologies|technology|the)\b\.?", re.I)


def clean_name(name: str) -> str:
    n = (name or "").replace(",", " ").replace(".", " ").replace("&", " and ")
    n = SUFFIXES.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip().upper()


def names_match(recipient: str, company: str) -> bool:
    r, c = clean_name(recipient), clean_name(company)
    if not r or not c:
        return False
    return r == c or r.startswith(c + " ") or c.startswith(r + " ") or (len(c) >= 6 and c in r)


def contract_awards(http: Http, company_name: str, lookback_days: int) -> dict | None:
    name = clean_name(company_name)
    if len(name) < 3:
        return None
    end = date.today()
    start = end - timedelta(days=lookback_days)
    body = {
        "filters": {
            "recipient_search_text": [name],
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
        },
        "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Start Date"],
        "limit": 100,
        "page": 1,
        "sort": "Award Amount",
        "order": "desc",
    }
    try:
        data = http.post_json(URL, body, ttl=24 * 3600)
    except Exception as e:  # noqa: BLE001
        log.debug("USASpending failed for %s: %s", company_name, e)
        return None
    results = (data or {}).get("results", [])
    matched = [r for r in results if names_match(r.get("Recipient Name", ""), company_name)]
    total = float(sum((r.get("Award Amount") or 0) for r in matched))
    agencies = sorted({r.get("Awarding Agency") for r in matched if r.get("Awarding Agency")})
    return {"total": total, "count": len(matched), "agencies": agencies[:3]}
