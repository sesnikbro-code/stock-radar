"""Price/volume signals computed on wide panels (dates x tickers) so the exact same code runs in the
daily scan and in the backtest (no look-ahead: every feature at date t uses data up to t only)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..util import SignalResult, clip, num, pct

FEATURES = ["close", "ret_5", "ret_21", "ret_63", "mom_6_1", "rs_63", "sma50", "sma200", "dist_high",
            "vol_ratio", "dollar_vol20", "atr14", "atr_pct", "rsi14", "squeeze", "history"]


def compute_panel_features(panel: dict[str, pd.DataFrame], spy_ticker: str = "SPY") -> dict[str, pd.DataFrame]:
    c, h, lo, v = panel["Close"], panel["High"], panel["Low"], panel["Volume"]
    f: dict[str, pd.DataFrame] = {"close": c}
    f["ret_5"] = c / c.shift(5) - 1
    f["ret_21"] = c / c.shift(21) - 1
    f["ret_63"] = c / c.shift(63) - 1
    f["mom_6_1"] = c.shift(21) / c.shift(126) - 1
    if spy_ticker in c.columns:
        spy63 = c[spy_ticker] / c[spy_ticker].shift(63) - 1
        f["rs_63"] = f["ret_63"].sub(spy63, axis=0)
    else:
        f["rs_63"] = f["ret_63"]
    f["sma50"] = c.rolling(50, min_periods=40).mean()
    f["sma200"] = c.rolling(200, min_periods=150).mean()
    f["dist_high"] = c / h.rolling(252, min_periods=60).max() - 1
    f["vol_ratio"] = v.rolling(5, min_periods=4).mean() / v.rolling(50, min_periods=30).mean()
    f["dollar_vol20"] = (c * v).rolling(20, min_periods=15).mean()
    prev = c.shift(1)
    tr = np.fmax(h - lo, np.fmax((h - prev).abs(), (lo - prev).abs()))
    f["atr14"] = tr.rolling(14, min_periods=10).mean()
    f["atr_pct"] = f["atr14"] / c
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    f["rsi14"] = rsi.where(loss != 0, 100.0).where(gain.notna())
    sma20 = c.rolling(20, min_periods=18).mean()
    bbw = 4 * c.rolling(20, min_periods=18).std() / sma20
    f["squeeze"] = bbw / bbw.rolling(126, min_periods=60).min()
    f["history"] = c.notna().cumsum()
    for k in list(f):
        f[k] = f[k].replace([np.inf, -np.inf], np.nan)
    return f


def eligibility(f: dict[str, pd.DataFrame], filters: dict) -> pd.DataFrame:
    return ((f["close"] >= filters["min_price"])
            & (f["dollar_vol20"] >= filters["min_dollar_volume"])
            & (f["history"] >= filters["min_history_days"]))


def _rank_signed(s: pd.Series) -> pd.Series:
    return 2 * s.rank(pct=True) - 1


def scores_at(f: dict[str, pd.DataFrame], t, eligible: pd.Series, weights: dict,
              exclude: tuple[str, ...] = ("SPY",)) -> pd.DataFrame:
    """Cross-sectional technical scores at date t for eligible tickers."""
    df = pd.DataFrame({k: f[k].loc[t] for k in FEATURES})
    mask = eligible.reindex(df.index).fillna(False).astype(bool)
    df = df[mask & ~df.index.isin(exclude)]
    if df.empty:
        return df
    acc = df["vol_ratio"].clip(upper=5) * np.sign(df["ret_5"].fillna(0))
    if len(df) >= 30:  # enough stocks to rank against each other
        mom = pd.concat([df["mom_6_1"].rank(pct=True), df["rs_63"].rank(pct=True)], axis=1).mean(axis=1, skipna=True)
        df["momentum"] = (2 * mom - 1).fillna(0)
        df["volume_accumulation"] = _rank_signed(acc).fillna(0)
    else:  # tiny list (single-stock analysis): absolute thresholds instead of ranks
        raw = pd.concat([df["mom_6_1"], df["rs_63"]], axis=1).mean(axis=1, skipna=True)
        df["momentum"] = (raw / 0.3).clip(-1, 1).fillna(0)
        df["volume_accumulation"] = (np.sign(acc) * (acc.abs() - 1).clip(lower=0) / 1.5).clip(-1, 1).fillna(0)
    near_high = (1 + df["dist_high"] / 0.15).clip(0, 1).fillna(0)
    trend = ((df["close"] > df["sma50"]).astype(float) + (df["sma50"] > df["sma200"]).astype(float)) / 2
    squeeze = ((1.6 - df["squeeze"]) / 0.6).clip(0, 1).fillna(0)
    raw = 0.4 * near_high + 0.3 * trend + 0.3 * squeeze
    df["breakout_setup"] = _rank_signed(raw).fillna(0) if len(df) >= 30 else ((raw - 0.35) / 0.5).clip(-1, 1)
    w = {k: float(weights.get(k, 1.0)) for k in ("momentum", "volume_accumulation", "breakout_setup")}
    tot = sum(w.values()) or 1.0
    df["tech_score"] = sum(df[k] * w[k] for k in w) / tot
    return df


def latest_scores(f, filters, weights) -> pd.DataFrame:
    elig = eligibility(f, filters)
    t = f["close"].index[-1]
    return scores_at(f, t, elig.loc[t], weights)


# ------------------------------------------------------------------ per-ticker signal objects
def technical_signals(tech: dict) -> dict[str, SignalResult]:
    out = {}
    r63, rs, mom = num(tech.get("ret_63")), num(tech.get("rs_63")), num(tech.get("momentum"), 0.0)
    reasons = []
    if r63 is not None and rs is not None and mom > 0.3:
        reasons.append(f"עלתה {pct(r63)} בשלושת החודשים האחרונים, {pct(abs(rs))} {'יותר' if rs > 0 else 'פחות'} מ־S&P 500")
    elif r63 is not None and mom < -0.3:
        reasons.append(f"מגמה חלשה: {'ירדה' if r63 < 0 else 'עלתה רק'} {pct(abs(r63))} בשלושה חודשים")
    out["momentum"] = SignalResult("momentum", clip(mom), 1.0, reasons, data={"ret_63": r63, "rs_63": rs})

    vr, r5, acc = num(tech.get("vol_ratio")), num(tech.get("ret_5")), num(tech.get("volume_accumulation"), 0.0)
    reasons = []
    if vr and vr >= 1.5 and r5 is not None and r5 > 0:
        reasons.append(f"מחזור המסחר בשבוע האחרון גבוה פי {vr:.1f} מהממוצע, עם עלייה של {pct(r5)} – סימן לצבירה")
    elif vr and vr >= 1.5 and r5 is not None and r5 < 0:
        reasons.append(f"מחזור גבוה פי {vr:.1f} עם ירידה במחיר – לחץ מכירות")
    out["volume_accumulation"] = SignalResult("volume_accumulation", clip(acc), 0.9, reasons, data={"vol_ratio": vr})

    dist, sq, bo = num(tech.get("dist_high")), num(tech.get("squeeze")), num(tech.get("breakout_setup"), 0.0)
    close, s50, s200 = num(tech.get("close")), num(tech.get("sma50")), num(tech.get("sma200"))
    reasons = []
    if bo > 0.2:
        if dist is not None and dist > -0.03:
            reasons.append("נסחרת בשיא של 52 שבועות")
        elif dist is not None and dist > -0.12:
            reasons.append(f"נסחרת {pct(-dist)} מתחת לשיא השנתי")
        if close and s50 and s200 and close > s50 > s200:
            reasons.append("מעל הממוצעים הנעים של 50 ו־200 ימים (מגמה עולה)")
        if sq is not None and sq < 1.15:
            reasons.append("טווח התנודה התכווץ לנמוך ביותר בחצי שנה – מצב שלעיתים קודם לתנועה חדה")
    elif bo < -0.4 and dist is not None and dist < -0.2:
        reasons.append(f"רחוקה {pct(-dist)} מהשיא השנתי" + (" ומתחת לממוצע 200 ימים" if close and s200 and close < s200 else ""))
    out["breakout_setup"] = SignalResult("breakout_setup", clip(bo), 0.8, reasons, data={"dist_high": dist, "squeeze": sq})
    return out
