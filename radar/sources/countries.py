"""Country statistics: IMF World Economic Outlook forecasts (DataMapper API, free) with
World Bank actuals as fallback. Used for US-listed foreign companies (ADRs, Israeli, Chinese...)."""
from __future__ import annotations

import logging
from datetime import date

from ..net import Http

log = logging.getLogger("radar.countries")

IMF_URL = "https://www.imf.org/external/datamapper/api/v1/{ind}/{iso}"
WB_URL = "https://api.worldbank.org/v2/country/{iso}/indicator/{ind}"

COUNTRY_ISO3 = {
    "united states": "USA", "israel": "ISR", "china": "CHN", "hong kong": "HKG", "taiwan": "TWN",
    "japan": "JPN", "south korea": "KOR", "korea": "KOR", "india": "IND", "singapore": "SGP",
    "united kingdom": "GBR", "ireland": "IRL", "germany": "DEU", "france": "FRA", "netherlands": "NLD",
    "switzerland": "CHE", "sweden": "SWE", "denmark": "DNK", "norway": "NOR", "finland": "FIN",
    "belgium": "BEL", "luxembourg": "LUX", "italy": "ITA", "spain": "ESP", "greece": "GRC",
    "canada": "CAN", "mexico": "MEX", "brazil": "BRA", "argentina": "ARG", "chile": "CHL",
    "colombia": "COL", "peru": "PER", "uruguay": "URY", "australia": "AUS", "new zealand": "NZL",
    "south africa": "ZAF", "turkey": "TUR", "united arab emirates": "ARE", "saudi arabia": "SAU",
    "indonesia": "IDN", "philippines": "PHL", "thailand": "THA", "vietnam": "VNM", "malaysia": "MYS",
    "kazakhstan": "KAZ", "poland": "POL", "cyprus": "CYP", "monaco": "MCO", "jersey": "JEY",
}

COUNTRY_HE = {
    "USA": "ארה\"ב", "ISR": "ישראל", "CHN": "סין", "HKG": "הונג קונג", "TWN": "טייוואן", "JPN": "יפן",
    "KOR": "דרום קוריאה", "IND": "הודו", "SGP": "סינגפור", "GBR": "בריטניה", "IRL": "אירלנד",
    "DEU": "גרמניה", "FRA": "צרפת", "NLD": "הולנד", "CHE": "שווייץ", "SWE": "שוודיה", "DNK": "דנמרק",
    "NOR": "נורבגיה", "FIN": "פינלנד", "BEL": "בלגיה", "LUX": "לוקסמבורג", "ITA": "איטליה", "ESP": "ספרד",
    "GRC": "יוון", "CAN": "קנדה", "MEX": "מקסיקו", "BRA": "ברזיל", "ARG": "ארגנטינה", "CHL": "צ'ילה",
    "COL": "קולומביה", "PER": "פרו", "URY": "אורוגוואי", "AUS": "אוסטרליה", "NZL": "ניו זילנד",
    "ZAF": "דרום אפריקה", "TUR": "טורקיה", "ARE": "איחוד האמירויות", "SAU": "סעודיה", "IDN": "אינדונזיה",
    "PHL": "פיליפינים", "THA": "תאילנד", "VNM": "וייטנאם", "MYS": "מלזיה", "KAZ": "קזחסטן", "POL": "פולין",
    "CYP": "קפריסין", "MCO": "מונקו", "JEY": "ג'רזי",
}

# Tax-haven domiciles say little about where the business actually operates.
SHELL_DOMICILES = {"cayman islands", "bermuda", "british virgin islands", "marshall islands", "bahamas",
                   "panama", "guernsey", "isle of man", "curacao", "gibraltar"}


def iso3_for(country_name: str | None) -> str | None:
    if not country_name:
        return None
    return COUNTRY_ISO3.get(country_name.strip().lower())


def parse_imf(data: dict, ind: str, iso: str) -> dict[int, float]:
    try:
        raw = data["values"][ind][iso]
    except (KeyError, TypeError):
        return {}
    out = {}
    for y, v in raw.items():
        try:
            out[int(y)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def parse_worldbank(data) -> dict[int, float]:
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return {}
    out = {}
    for row in data[1]:
        try:
            if row.get("value") is not None:
                out[int(row["date"])] = float(row["value"])
        except (TypeError, ValueError, KeyError):
            continue
    return out


def country_stats(http: Http, iso: str) -> dict | None:
    """Returns growth this year, next year (forecast) and inflation. None if nothing is available."""
    y = date.today().year
    stats = {"iso": iso, "source": None}
    try:
        g = parse_imf(http.get(IMF_URL.format(ind="NGDP_RPCH", iso=iso), as_json=True, ttl=7 * 86400) or {}, "NGDP_RPCH", iso)
        inf = parse_imf(http.get(IMF_URL.format(ind="PCPIPCH", iso=iso), as_json=True, ttl=7 * 86400) or {}, "PCPIPCH", iso)
        if y in g and (y + 1) in g:
            stats.update(growth_now=g[y], growth_next=g[y + 1], inflation=inf.get(y), source="IMF")
            return stats
    except Exception as e:  # noqa: BLE001
        log.debug("IMF failed for %s: %s", iso, e)
    try:
        g = parse_worldbank(http.get(WB_URL.format(iso=iso, ind="NY.GDP.MKTP.KD.ZG"),
                                     params={"format": "json", "per_page": 10, "mrv": 5}, as_json=True, ttl=7 * 86400))
        inf = parse_worldbank(http.get(WB_URL.format(iso=iso, ind="FP.CPI.TOTL.ZG"),
                                       params={"format": "json", "per_page": 10, "mrv": 5}, as_json=True, ttl=7 * 86400))
        if len(g) >= 2:
            years = sorted(g)
            stats.update(growth_now=g[years[-1]], growth_next=g[years[-1]], growth_prev=g[years[-2]],
                         inflation=inf.get(max(inf)) if inf else None, source="World Bank")
            # no forecast: use last change as momentum proxy
            stats["growth_next"] = g[years[-1]] + (g[years[-1]] - g[years[-2]]) * 0.5
            return stats
    except Exception as e:  # noqa: BLE001
        log.debug("World Bank failed for %s: %s", iso, e)
    return None
