"""Wars and geopolitical tension from GDELT (global news monitoring, free, no key).

For each theme we read the share of world news coverage over 90 days and compare the last week
with the previous two months. A rising share = escalation. Each theme maps to sector tilts.
"""
from __future__ import annotations

import logging

import numpy as np

from ..net import Http

log = logging.getLogger("radar.gdelt")

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Sector groups are matched against yfinance industry / business summary (lowercase).
SECTOR_GROUPS = {
    "defense": {"industry": ["aerospace & defense"], "summary": ["defense contractor", "missile", "munitions", "military"]},
    "oil_gas": {"industry": ["oil & gas"], "summary": []},
    "gold": {"industry": ["gold", "other precious metals"], "summary": []},
    "shipping": {"industry": ["marine shipping"], "summary": ["tanker", "dry bulk"]},
    "airlines": {"industry": ["airlines"], "summary": []},
    "travel": {"industry": ["travel services", "resorts & casinos", "lodging", "leisure"], "summary": ["cruise"]},
    "fertilizer": {"industry": ["agricultural inputs"], "summary": ["fertilizer", "potash"]},
    "semis": {"industry": ["semiconductor"], "summary": []},
    "cyber": {"industry": [], "summary": ["cybersecurity", "cyber security", "threat detection", "zero trust"]},
    "steel": {"industry": ["steel"], "summary": []},
    "importers": {"industry": ["apparel retail", "footwear", "specialty retail", "department stores",
                               "home improvement retail", "furnishings"], "summary": []},
    "autos": {"industry": ["auto manufacturers", "auto parts"], "summary": []},
}

GROUP_HE = {
    "defense": "ביטחון", "oil_gas": "נפט וגז", "gold": "זהב", "shipping": "ספנות", "airlines": "תעופה",
    "travel": "תיירות ופנאי", "fertilizer": "דשנים", "semis": "שבבים", "cyber": "סייבר", "steel": "פלדה",
    "importers": "קמעונאות מיובאת", "autos": "רכב",
}

# One OR-group per query (GDELT syntax limitation) plus plain AND terms / operators.
THEMES = {
    "global_conflict": {
        "he": "מלחמות בעולם (כללי)",
        "query": "theme:ARMEDCONFLICT",
        "tilts": {"defense": 1.0, "gold": 0.4, "oil_gas": 0.3, "airlines": -0.5, "travel": -0.4},
    },
    "middle_east": {
        "he": "המזרח התיכון",
        "query": "theme:ARMEDCONFLICT (Israel OR Iran OR Gaza OR Lebanon OR Yemen OR Hezbollah OR Houthi)",
        "tilts": {"oil_gas": 0.8, "defense": 0.6, "shipping": 0.5, "airlines": -0.6, "travel": -0.3},
    },
    "russia_ukraine": {
        "he": "רוסיה-אוקראינה",
        "query": "theme:ARMEDCONFLICT (Ukraine OR Russia OR Kyiv OR Kremlin)",
        "tilts": {"defense": 0.8, "oil_gas": 0.5, "fertilizer": 0.6},
    },
    "taiwan_china": {
        "he": "סין-טייוואן",
        "query": 'military (Taiwan OR "South China Sea" OR "Taiwan Strait")',
        "tilts": {"semis": -0.6, "defense": 0.5},
    },
    "shipping_lanes": {
        "he": "נתיבי שיט (ים סוף, הורמוז, סואץ)",
        "query": 'shipping ("Red Sea" OR Hormuz OR Suez OR "Bab el-Mandeb")',
        "tilts": {"shipping": 0.9, "oil_gas": 0.3, "importers": -0.3},
    },
    "cyber": {
        "he": "מתקפות סייבר",
        "query": '(cyberattack OR ransomware OR "data breach" OR "cyber attack")',
        "tilts": {"cyber": 0.8},
    },
    "tariffs": {
        "he": "מכסים ומלחמת סחר",
        "query": '(tariff OR tariffs OR "trade war")',
        "tilts": {"steel": 0.5, "importers": -0.5, "autos": -0.4, "semis": -0.2},
    },
}


def daily_series(data: dict) -> list[tuple[str, float]]:
    """Aggregate GDELT timeline points (may be 15-min/hourly/daily) to daily means."""
    try:
        points = data["timeline"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return []
    buckets: dict[str, list[float]] = {}
    for p in points:
        d = str(p.get("date", ""))[:8]
        try:
            buckets.setdefault(d, []).append(float(p.get("value", 0) or 0))
        except (TypeError, ValueError):
            continue
    return [(d, float(np.mean(v))) for d, v in sorted(buckets.items()) if d]


def intensity_from_series(series: list[tuple[str, float]], recent_days: int = 7, base_days: int = 60) -> dict | None:
    if len(series) < recent_days + 14:
        return None
    vals = np.array([v for _, v in series], dtype=float)
    recent = vals[-recent_days:].mean()
    base_slice = vals[-(recent_days + base_days):-recent_days]
    base = base_slice.mean() if len(base_slice) else 0.0
    if base <= 0:
        return None
    ratio = recent / base
    escalation = float(np.clip((ratio - 1.0) / 0.75, -1.0, 1.0))
    return {"recent": float(recent), "base": float(base), "ratio": float(ratio), "escalation": escalation,
            "last_date": series[-1][0]}


def theme_intensity(http: Http, query: str) -> dict | None:
    params = {"query": query, "mode": "timelinevol", "format": "json", "timespan": "90d"}
    try:
        data = http.get(DOC_API, params=params, as_json=True, ttl=6 * 3600)
    except Exception as e:  # noqa: BLE001
        log.warning("GDELT לא החזיר נתונים עבור '%s': %s", query, e)
        return None
    if not data:
        return None
    return intensity_from_series(daily_series(data))


def all_themes(http: Http, themes: dict | None = None) -> dict[str, dict]:
    themes = themes or THEMES
    out = {}
    for key, th in themes.items():
        res = theme_intensity(http, th["query"])
        if res:
            out[key] = {**res, "he": th["he"], "tilts": th["tilts"]}
            log.info("מתיחות %s: פי %.2f מהממוצע", th["he"], res["ratio"])
    return out


def country_conflict(http: Http, country_name: str) -> dict | None:
    q = f'theme:ARMEDCONFLICT "{country_name}"' if " " in country_name else f"theme:ARMEDCONFLICT {country_name}"
    return theme_intensity(http, q)


def match_groups(industry: str, summary: str) -> list[str]:
    ind, summ = (industry or "").lower(), (summary or "").lower()
    groups = []
    for g, rule in SECTOR_GROUPS.items():
        if any(k in ind for k in rule["industry"]) or any(k in summ for k in rule["summary"]):
            groups.append(g)
    return groups
