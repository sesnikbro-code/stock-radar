"""Market-wide context turned into per-stock signals: macro regime, wars/geopolitics, country
statistics and US government contracts."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..sources.countries import COUNTRY_HE, SHELL_DOMICILES, iso3_for
from ..sources.gdelt import GROUP_HE, match_groups
from ..util import SignalResult, clip, money_he, num, pct


# ------------------------------------------------------------------ macro regime
def _last(s: pd.Series | None):
    return float(s.dropna().iloc[-1]) if s is not None and not s.dropna().empty else None


def _change(s: pd.Series | None, n: int):
    if s is None:
        return None
    s = s.dropna()
    if len(s) <= n:
        return None
    return float(s.iloc[-1] - s.iloc[-1 - n])


def compute_regime(series: dict[str, pd.Series], spy_close: pd.Series | None) -> dict:
    comps, notes = [], []

    vix = _last(series.get("VIXCLS"))
    if vix is not None:
        comps.append((clip((20 - vix) / 10), 1.0))
        notes.append(f"מדד הפחד VIX: {vix:.1f} ({'רגוע' if vix < 16 else 'לחוץ' if vix > 25 else 'בינוני'})")

    hy = series.get("BAMLH0A0HYM2")
    hy_chg = _change(hy, 21)
    if hy_chg is not None:
        comps.append((clip(-hy_chg / 0.75), 0.8))
        notes.append(f"מרווח אג\"ח זבל {_last(hy):.2f}% ({'מתרחב' if hy_chg > 0.15 else 'מצטמצם' if hy_chg < -0.15 else 'יציב'} בחודש האחרון)")

    un = series.get("UNRATE")
    if un is not None and len(un.dropna()) >= 15:
        u3 = un.dropna().rolling(3).mean()
        sahm = float(u3.iloc[-1] - u3.iloc[-13:].min())
        comps.append((clip(0.5 - sahm / 0.5, -1, 0.5), 0.8))
        notes.append(f"אבטלה {_last(un):.1f}%" + (" – סימן אזהרה למיתון (כלל Sahm)" if sahm >= 0.5 else ""))

    rate_trend = _change(series.get("DGS10"), 63)
    if rate_trend is not None:
        comps.append((clip(-rate_trend / 0.5) * 0.5, 0.5))
        notes.append(f"תשואת 10 שנים {_last(series.get('DGS10')):.2f}% ({'עולה' if rate_trend > 0.15 else 'יורדת' if rate_trend < -0.15 else 'יציבה'} ב־3 חודשים)")

    curve = _last(series.get("T10Y2Y"))
    if curve is not None:
        comps.append((-0.3 if curve < 0 else 0.1, 0.4))

    cpi = series.get("CPIAUCSL")
    if cpi is not None and len(cpi.dropna()) > 13:
        c = cpi.dropna()
        yoy = float(c.iloc[-1] / c.iloc[-13] - 1)
        comps.append((-0.3 if yoy > 0.04 else 0.1 if yoy < 0.03 else 0.0, 0.4))
        notes.append(f"אינפלציה שנתית {pct(yoy, 1)}")

    if spy_close is not None and len(spy_close.dropna()) > 200:
        s = spy_close.dropna()
        sma200 = s.rolling(200).mean().iloc[-1]
        above = s.iloc[-1] > sma200
        comps.append((0.6 if above else -0.6, 1.2))
        notes.append(f"S&P 500 {'מעל ' if above else 'מתחת ל'}ממוצע 200 ימים")

    score = sum(v * w for v, w in comps) / sum(w for _, w in comps) if comps else 0.0
    label = "חיובי (תיאבון לסיכון)" if score > 0.25 else "שלילי (בריחה מסיכון)" if score < -0.25 else "ניטרלי"
    return {"score": float(score), "label": label, "notes": notes, "rate_trend": rate_trend, "risk_off": score < -0.25}


def compute_beta(close: pd.Series, spy: pd.Series, days: int = 252) -> float | None:
    r = close.pct_change().dropna().iloc[-days:]
    m = spy.pct_change().reindex(r.index).dropna()
    r = r.reindex(m.index)
    if len(r) < 60 or m.var() == 0:
        return None
    return float(np.cov(r, m)[0, 1] / m.var())


def macro_signal(regime: dict, beta: float | None, market_cap: float | None, sector: str) -> SignalResult:
    res = SignalResult("macro")
    if not regime:
        return res
    b = beta if beta is not None else 1.0
    s = regime["score"] * clip((b - 0.9) / 0.8)
    if abs(s) > 0.1:
        res.reasons.append(f"מצב השוק {regime['label']}, והמניה {'תנודתית מהשוק' if b > 1.2 else 'דפנסיבית'} (בטא {b:.1f})")
    rt = regime.get("rate_trend")
    sensitive = (market_cap or 0) < 2e9 or sector in ("Real Estate", "Utilities", "Technology")
    if rt is not None and sensitive and abs(rt) > 0.15:
        s += -0.2 if rt > 0 else 0.2
        res.reasons.append("הריבית לטווח ארוך " + ("עולה – לוחץ על " if rt > 0 else "יורדת – תומך ב") + "מניות צמיחה וחברות קטנות")
    res.score, res.conf = clip(s), 0.6
    if regime.get("risk_off"):
        res.risk_add = 10
        res.risk_reasons.append("השוק במצב בריחה מסיכון")
    res.data = {"beta": beta}
    return res


# ------------------------------------------------------------------ geopolitics
def geopolitics_signal(themes: dict, industry: str, summary: str) -> SignalResult:
    res = SignalResult("geopolitics")
    groups = match_groups(industry, summary)
    if not groups or not themes:
        return res
    contribs = []
    for th in themes.values():
        for g in groups:
            w = th["tilts"].get(g)
            if w:
                contribs.append((th["escalation"] * w, th, g))
    if not contribs:
        return res
    res.score = clip(sum(c for c, _, _ in contribs))
    res.conf = 0.7
    for c, th, g in sorted(contribs, key=lambda x: abs(x[0]), reverse=True)[:2]:
        if abs(c) < 0.05:
            continue
        trend = "עלתה" if th["escalation"] > 0 else "ירדה"
        effect = "חיובי" if c > 0 else "שלילי"
        res.reasons.append(f"המתיחות סביב {th['he']} {trend} (פי {th['ratio']:.1f} מהממוצע) – בדרך כלל {effect} לענף ה{GROUP_HE[g]}")
    res.data = {"groups": groups}
    return res


# ------------------------------------------------------------------ countries
def score_country(stats: dict, conflict: dict | None) -> tuple[float, list[str]]:
    g_now, g_next = num(stats.get("growth_now"), 0), num(stats.get("growth_next"), 0)
    infl = num(stats.get("inflation"), 3)
    s = 0.4 * clip((g_now - 2.0) / 3) + 0.4 * clip((g_next - g_now) / 1.5) - 0.2 * clip((infl - 5) / 10, 0, 1)
    name = COUNTRY_HE.get(stats.get("iso"), stats.get("iso"))
    reasons = [f"צמיחה ב{name}: {g_now:.1f}% השנה, תחזית {g_next:.1f}% לשנה הבאה ({stats.get('source')}), אינפלציה {infl:.1f}%"]
    if conflict and conflict.get("escalation", 0) > 0.2:
        s -= 0.4 * conflict["escalation"]
        reasons.append(f"עלייה בדיווחים על עימות צבאי הקשור ל{name}")
    return clip(s), reasons


def country_signal(country: str | None, countries: dict) -> SignalResult:
    res = SignalResult("country")
    if not country or country.strip().lower() in SHELL_DOMICILES:
        return res
    iso = iso3_for(country)
    if not iso or iso == "USA" or iso not in countries:
        return res
    c = countries[iso]
    res.score, res.reasons = c["score"], list(c["reasons"])
    res.conf = 0.6
    res.data = {"iso": iso}
    return res


# ------------------------------------------------------------------ government contracts
def gov_signal(gov: dict | None, market_cap: float | None, lookback_days: int) -> SignalResult:
    res = SignalResult("gov_contracts")
    if not gov or not gov.get("total") or not market_cap:
        return res
    total = gov["total"]
    ratio = total / market_cap
    s = clip(ratio / 0.05, 0, 1) * (0.3 if total < 1e6 else 1.0)
    res.score, res.conf = s, 0.8
    agency = f", בעיקר מ־{gov['agencies'][0]}" if gov.get("agencies") else ""
    res.reasons.append(f"קיבלה חוזים ממשלתיים בכ־{money_he(total)} ב־{lookback_days} הימים האחרונים "
                       f"({pct(ratio, 1)} משווי השוק){agency}")
    res.data = dict(gov, ratio=ratio)
    return res
