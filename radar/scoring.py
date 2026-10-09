"""Combine signals into an upside score (0-100) and a risk score (0-100), plus position sizing."""
from __future__ import annotations

import math

from .util import SignalResult, TickerData, clip, num, pct


def composite(signals: dict[str, SignalResult], weights: dict[str, float]) -> tuple[float, float]:
    """Confidence-weighted mean of signal scores, shrunk toward neutral when little data is available.
    Returns (upside score 0-100, coverage 0-1)."""
    num_, den, possible = 0.0, 0.0, 0.0
    for name, w in weights.items():
        possible += w
        s = signals.get(name)
        if s is None or s.conf <= 0:
            continue
        num_ += w * s.conf * s.score
        den += w * s.conf
    if den == 0:
        return 50.0, 0.0
    coverage = den / possible if possible else 0.0
    x = (num_ / den) * min(1.0, coverage / 0.5)
    return 50.0 + 50.0 * clip(x), coverage


def risk_score(td: TickerData, signals: dict[str, SignalResult], filters: dict) -> tuple[float, list[str]]:
    t = td.tech
    pts, reasons = 0.0, []
    atr_pct = num(t.get("atr_pct"))
    if atr_pct is not None:
        v = clip((atr_pct - 0.02) / 0.08, 0, 1) * 30
        pts += v
        if v >= 12:
            reasons.append(f"תנודתיות יומית גבוהה (כ־{pct(atr_pct, 1)} ביום בממוצע)")
    rsi = num(t.get("rsi14"))
    if rsi is not None and rsi > 75:
        pts += clip((rsi - 75) / 15, 0, 1) * 15
        reasons.append(f"קנויה־יתר בטווח הקצר (RSI {rsi:.0f}) – סיכון לתיקון")
    close, sma50 = num(t.get("close")), num(t.get("sma50"))
    if close and sma50 and close / sma50 - 1 > 0.3:
        pts += 10
        reasons.append(f"נסחרת {pct(close / sma50 - 1)} מעל הממוצע של 50 ימים – מתוחה")
    dist = num(t.get("dist_high"))
    if dist is not None and dist < -0.5:
        pts += 10
        reasons.append(f"ירדה {pct(-dist)} מהשיא השנתי – ייתכן שעדיין בנפילה")
    mcap = num(td.info.get("marketCap"))
    if mcap:
        if mcap < 3e8:
            pts += 10
            reasons.append("חברה קטנה מאוד (פחות מ־300 מיליון $)")
        elif mcap < 1e9:
            pts += 5
    dv = num(t.get("dollar_vol20"))
    if dv and dv < 2 * filters["min_dollar_volume"]:
        pts += 5
        reasons.append("נזילות נמוכה יחסית")
    for s in signals.values():
        if s.risk_add:
            pts += s.risk_add
            reasons.extend(s.risk_reasons)
    return min(100.0, pts), reasons


def expected_move_3m(atr_pct: float | None) -> float | None:
    """Rough 1-sigma 3-month move implied by daily ATR (ATR ≈ 1.2 x daily std)."""
    if not atr_pct:
        return None
    return atr_pct / 1.2 * math.sqrt(63)


def position_plan(price: float, atr: float | None, risk_cfg: dict) -> dict:
    account = float(risk_cfg["account_size"])
    if not price or not atr:
        return {}
    stop = max(0.01, price - risk_cfg["atr_stop_multiple"] * atr)
    risk_per_share = price - stop
    risk_amount = account * risk_cfg["risk_per_trade_pct"] / 100
    shares = int(risk_amount // risk_per_share) if risk_per_share > 0 else 0
    max_value = account * risk_cfg["max_position_pct"] / 100
    if shares * price > max_value:
        shares = int(max_value // price)
    target_r = float(risk_cfg.get("target_r") or 0)
    target = price + target_r * risk_per_share if target_r > 0 else None
    return {"stop": round(stop, 2), "stop_pct": stop / price - 1, "shares": shares,
            "target": round(target, 2) if target else None, "target_pct": (target / price - 1) if target else None,
            "value": round(shares * price, 2), "risk_amount": round(shares * risk_per_share, 2),
            "pct_of_account": shares * price / account if account else 0}
