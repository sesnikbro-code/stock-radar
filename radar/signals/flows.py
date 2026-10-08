"""Positioning signals: short interest (squeeze fuel) and unusual options activity."""
from __future__ import annotations

import statistics

from ..util import SignalResult, clip, num, pct


def short_squeeze_signal(info: dict, tech: dict) -> SignalResult:
    res = SignalResult("short_squeeze")
    spf = num(info.get("shortPercentOfFloat"))
    shares_short, prior = num(info.get("sharesShort")), num(info.get("sharesShortPriorMonth"))
    if spf is None and shares_short and num(info.get("floatShares")):
        spf = shares_short / num(info.get("floatShares"))
    if spf is None:
        return res
    if spf > 1.5:  # some feeds report percent instead of fraction
        spf /= 100
    dtc = num(info.get("shortRatio"), 0.0) or 0.0
    base = clip((spf - 0.08) / 0.22, 0, 1)
    fuel = base * (0.6 + 0.4 * clip(dtc / 8, 0, 1))
    chg = (shares_short / prior - 1) if shares_short and prior else None
    close, sma50, r21 = num(tech.get("close")), num(tech.get("sma50")), num(tech.get("ret_21"), 0.0)
    uptrend = bool(close and sma50 and close > sma50 and r21 > 0)
    if uptrend:
        res.score = clip(fuel * (1.1 if chg and chg > 0.1 else 1.0))
    else:
        res.score = clip(-0.5 * fuel)
    res.conf = 0.8
    res.data = {"short_pct_float": spf, "days_to_cover": dtc, "short_change": chg}
    if spf >= 0.10:
        txt = f"{pct(spf)} מהמניות הצפות בשורט, {dtc:.1f} ימי כיסוי"
        if uptrend:
            txt += " והמחיר במגמת עלייה – פוטנציאל לשורט סקוויז"
        else:
            txt += " והמחיר יורד – ייתכן שהשורטיסטים צודקים"
        res.reasons.append(txt)
    if spf >= 0.25:
        res.risk_add = 8
        res.risk_reasons.append("שורט גבוה מאוד = תנודתיות חדה לשני הכיוונים")
    return res


def options_signal(opt: dict | None, tech: dict, history: list[float] | None = None) -> SignalResult:
    res = SignalResult("options_flow")
    if not opt:
        return res
    cv, pv = opt.get("call_vol", 0) or 0, opt.get("put_vol", 0) or 0
    coi, poi = opt.get("call_oi", 0) or 0, opt.get("put_oi", 0) or 0
    tot = cv + pv
    res.data = dict(opt)
    if tot < 200:
        res.conf = 0.2
        return res
    vol_oi = tot / (coi + poi + 1)
    unusual = clip((vol_oi - 0.25) / 0.75, 0, 1)
    hist_ratio = None
    if history and len(history) >= 10:
        med = statistics.median(history)
        if med > 0:
            hist_ratio = tot / med
            unusual = max(unusual, clip((hist_ratio - 1.5) / 2.5, 0, 1))
    if cv >= pv:
        direction = clip((cv / (pv + 1) - 1.0) / 2.0, 0, 1)
    else:
        direction = -clip((pv / (cv + 1) - 1.0) / 2.0, 0, 1)
    otm_share = (opt.get("otm_call_vol", 0) or 0) / (cv + 1)
    res.score = clip(unusual * direction + 0.2 * direction * otm_share)
    res.conf = 0.7
    res.data.update(vol_oi=vol_oi, hist_ratio=hist_ratio)
    if unusual > 0.3 and abs(direction) > 0.2:
        side = "עלייה (קולים)" if direction > 0 else "ירידה (פוטים)"
        mult = f"פי {hist_ratio:.1f} מהממוצע שלה" if hist_ratio else f"{vol_oi:.1f} מהפוזיציות הפתוחות"
        res.reasons.append(f"פעילות אופציות חריגה ({mult}), רובה הימור על {side}")
    iv = num(opt.get("atm_iv"))
    if iv and iv > 1.0:
        res.risk_add = 5
        res.risk_reasons.append(f"שוק האופציות מתמחר תנודתיות גבוהה מאוד ({pct(iv)} בשנה)")
    return res
