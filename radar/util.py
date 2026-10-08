"""Small shared helpers and data containers."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd


def clip(x, lo=-1.0, hi=1.0) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(x):
        return 0.0
    return max(lo, min(hi, x))


def num(x, default=None):
    try:
        v = float(x)
        return default if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return default


def money_he(v: float | None) -> str:
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e9:
        s = f"{a / 1e9:.1f} מיליארד $"
    elif a >= 1e6:
        s = f"{a / 1e6:.1f} מיליון $"
    elif a >= 1e3:
        s = f"{a / 1e3:.0f} אלף $"
    else:
        s = f"{a:.0f} $"
    return ("-" if v < 0 else "") + s


def pct(v: float | None, digits: int = 0, sign: bool = False) -> str:
    if v is None:
        return "-"
    fmt = f"{{:{'+' if sign else ''}.{digits}%}}"
    out = fmt.format(v)
    # LRM keeps "+12%" / "-12%" readable inside right-to-left Hebrew text
    return "\u200e" + out if out[:1] in "+-" else out


@dataclass
class SignalResult:
    name: str
    score: float = 0.0          # -1 (bearish) .. +1 (bullish)
    conf: float = 0.0           # 0 = no data / ignore, 1 = full confidence
    reasons: list[str] = field(default_factory=list)
    risk_add: float = 0.0       # points added to the 0-100 risk score
    risk_reasons: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class TickerData:
    ticker: str
    name: str
    cik: int | None
    price: float
    tech: dict                      # technical features + cross-sectional scores at the scan date
    prices: pd.DataFrame            # OHLCV history for this ticker
    info: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    insider_rows: list[dict] = field(default_factory=list)
    insider_source: str = ""
    gov: dict | None = None
    news: list[dict] = field(default_factory=list)
    beta: float | None = None
    options_history: list[float] = field(default_factory=list)
    earnings: pd.DataFrame | None = None   # past quarterly reports: date, surprise


@dataclass
class MarketContext:
    asof: date
    regime: dict
    themes: dict
    countries: dict
    firm_accuracy: dict
    spy_close: pd.Series
    is_demo: bool = False


SECTOR_HE = {
    "Technology": "טכנולוגיה", "Healthcare": "בריאות", "Financial Services": "פיננסים", "Industrials": "תעשייה",
    "Consumer Cyclical": "צריכה מחזורית", "Consumer Defensive": "צריכה בסיסית", "Energy": "אנרגיה",
    "Basic Materials": "חומרי גלם", "Real Estate": "נדל\"ן", "Utilities": "תשתיות", "Communication Services": "תקשורת",
}
INDUSTRY_HE = {
    "Aerospace & Defense": "ביטחון ותעופה", "Semiconductors": "שבבים", "Semiconductor Equipment & Materials": "ציוד לשבבים",
    "Software - Infrastructure": "תוכנת תשתית", "Software - Application": "תוכנה יישומית", "Biotechnology": "ביוטכנולוגיה",
    "Drug Manufacturers - General": "תרופות", "Drug Manufacturers - Specialty & Generic": "תרופות גנריות",
    "Medical Devices": "מכשור רפואי", "Diagnostics & Research": "אבחון ומחקר", "Oil & Gas E&P": "חיפושי נפט וגז",
    "Oil & Gas Integrated": "נפט וגז משולב", "Oil & Gas Midstream": "הולכת נפט וגז", "Oil & Gas Refining & Marketing": "זיקוק נפט",
    "Oil & Gas Equipment & Services": "שירותי נפט וגז", "Marine Shipping": "ספנות", "Gold": "זהב",
    "Other Precious Metals & Mining": "מתכות יקרות", "Agricultural Inputs": "דשנים וחקלאות", "Airlines": "תעופה אזרחית",
    "Specialty Retail": "קמעונאות", "Internet Retail": "מסחר מקוון", "Banks - Regional": "בנקים אזוריים",
    "Banks - Diversified": "בנקים", "Internet Content & Information": "אינטרנט ותוכן", "Steel": "פלדה",
    "Auto Manufacturers": "יצרניות רכב", "Auto Parts": "חלקי רכב", "Solar": "אנרגיה סולארית", "Uranium": "אורניום",
    "Utilities - Regulated Electric": "חשמל", "Electrical Equipment & Parts": "ציוד חשמלי",
    "Specialty Industrial Machinery": "מכונות תעשייתיות", "Communication Equipment": "ציוד תקשורת",
    "Computer Hardware": "חומרת מחשבים", "Information Technology Services": "שירותי מחשוב",
    "Electronic Components": "רכיבים אלקטרוניים", "Scientific & Technical Instruments": "מכשור מדעי",
    "Capital Markets": "שוק ההון", "Asset Management": "ניהול נכסים", "Insurance - Property & Casualty": "ביטוח",
    "Credit Services": "אשראי", "Travel Services": "תיירות", "Resorts & Casinos": "נופש וקזינו", "Restaurants": "מסעדות",
    "Entertainment": "בידור", "Telecom Services": "תקשורת", "Building Products & Equipment": "מוצרי בנייה",
    "Engineering & Construction": "הנדסה ובנייה", "Trucking": "הובלה", "Railroads": "רכבות",
    "Integrated Freight & Logistics": "לוגיסטיקה", "Copper": "נחושת", "Chemicals": "כימיקלים",
    "Specialty Chemicals": "כימיקלים מיוחדים", "Packaged Foods": "מזון", "Beverages - Non-Alcoholic": "משקאות",
    "Household & Personal Products": "מוצרי צריכה", "Apparel Retail": "אופנה", "Footwear & Accessories": "הנעלה",
    "Medical Instruments & Supplies": "ציוד רפואי", "Healthcare Plans": "ביטוח בריאות", "Shell Companies": "חברות מעטפת",
    "REIT - Industrial": "נדל\"ן תעשייתי", "REIT - Office": "נדל\"ן משרדים", "REIT - Residential": "נדל\"ן מגורים",
}


def he_sector(x: str | None) -> str:
    return SECTOR_HE.get(x or "", x or "")


def he_industry(x: str | None) -> str:
    return INDUSTRY_HE.get(x or "", x or "")
