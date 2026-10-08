"""Analyst signals: estimate revisions (what actually moves prices), rating changes weighted by each
firm's historical hit rate, and consensus price target."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..util import SignalResult, clip, num, pct

POSITIVE = {"buy", "strong buy", "outperform", "overweight", "positive", "accumulate", "add", "sector outperform",
            "market outperform", "conviction buy", "top pick", "long-term buy", "speculative buy"}
NEGATIVE = {"sell", "strong sell", "underperform", "underweight", "reduce", "negative", "sector underperform",
            "market underperform"}
PERIOD_HE = {"0q": "לרבעון הנוכחי", "+1q": "לרבעון הבא", "0y": "לשנה הנוכחית", "+1y": "לשנה הבאה"}


def _col(df: pd.DataFrame, name: str):
    for c in df.columns:
        if str(c).lower() == name.lower():
            return c
    return None


def _grade_dir(g) -> int:
    g = str(g or "").strip().lower()
    return 1 if g in POSITIVE else -1 if g in NEGATIVE else 0


# ------------------------------------------------------------------ estimate revisions
def revisions_signal(eps_trend: pd.DataFrame | None, eps_revisions: pd.DataFrame | None) -> SignalResult:
    res = SignalResult("analyst_revisions")
    weights = {"0q": 0.15, "+1q": 0.2, "0y": 0.3, "+1y": 0.35}
    r30s, r90s, best = [], [], None
    if isinstance(eps_trend, pd.DataFrame) and not eps_trend.empty:
        ccur, c30, c90 = _col(eps_trend, "current"), _col(eps_trend, "30daysAgo"), _col(eps_trend, "90daysAgo")
        for per, w in weights.items():
            if per not in eps_trend.index or ccur is None:
                continue
            cur = num(eps_trend.loc[per, ccur])
            d30 = num(eps_trend.loc[per, c30]) if c30 is not None else None
            d90 = num(eps_trend.loc[per, c90]) if c90 is not None else None
            if cur is None:
                continue
            if d30 is not None:
                r = (cur - d30) / max(abs(d30), 0.05)
                r30s.append((r, w))
                if per in ("+1y", "0y") and (best is None or per == "+1y"):
                    best = (per, r)
            if d90 is not None:
                r90s.append(((cur - d90) / max(abs(d90), 0.05), w))
    level = None
    if r30s or r90s:
        r30 = sum(r * w for r, w in r30s) / sum(w for _, w in r30s) if r30s else 0.0
        r90 = sum(r * w for r, w in r90s) / sum(w for _, w in r90s) if r90s else 0.0
        level = clip(r30 / 0.08) * 0.6 + clip(r90 / 0.15) * 0.4
        res.data.update(rev30=r30, rev90=r90)
    breadth = None
    if isinstance(eps_revisions, pd.DataFrame) and not eps_revisions.empty:
        cu, cd = _col(eps_revisions, "upLast30days"), _col(eps_revisions, "downLast30days")
        if cu is not None and cd is not None:
            rows = [p for p in ("0y", "+1y") if p in eps_revisions.index] or list(eps_revisions.index)
            up = sum(num(eps_revisions.loc[p, cu], 0) for p in rows)
            down = sum(num(eps_revisions.loc[p, cd], 0) for p in rows)
            breadth = (up - down) / (up + down + 2)
            res.data.update(up=up, down=down)
            if up + down > 0:
                res.reasons.append(f"בחודש האחרון {int(up)} עדכוני תחזית כלפי מעלה ו־{int(down)} כלפי מטה")
    if level is None and breadth is None:
        return res
    score = (0.7 * level if level is not None else 0) + (0.3 * clip(breadth * 1.5) if breadth is not None else 0)
    if level is None:
        score = clip(breadth * 1.5) * 0.7
    res.score = clip(score)
    res.conf = 0.9 if level is not None else 0.5
    if best and abs(best[1]) >= 0.02:
        verb = "עלתה" if best[1] > 0 else "ירדה"
        res.reasons.insert(0, f"תחזית הרווח {PERIOD_HE[best[0]]} {verb} {pct(abs(best[1]))} ב־30 הימים האחרונים")
    return res


# ------------------------------------------------------------------ ratings + firm accuracy
def normalize_upgrades(df: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=["date", "firm", "to", "from", "action", "pt_now", "pt_prior"])
    d = df.copy()
    if not isinstance(d.index, pd.RangeIndex):
        d = d.reset_index()
    cols = {str(c).lower(): c for c in d.columns}
    get = lambda name: d[cols[name]] if name in cols else pd.Series([None] * len(d))  # noqa: E731
    out = pd.DataFrame({
        "date": pd.to_datetime(get("gradedate") if "gradedate" in cols else get("date"), errors="coerce"),
        "firm": get("firm").astype(str),
        "to": get("tograde"),
        "from": get("fromgrade"),
        "action": get("action").astype(str).str.lower(),
        "pt_now": pd.to_numeric(get("currentpricetarget"), errors="coerce"),
        "pt_prior": pd.to_numeric(get("priorpricetarget"), errors="coerce"),
    })
    out["date"] = out["date"].dt.tz_localize(None) if getattr(out["date"].dt, "tz", None) else out["date"]
    return out.dropna(subset=["date"])


def call_direction(row) -> int:
    a = row["action"]
    if a == "up":
        return 1
    if a == "down":
        return -1
    if a == "init":
        return _grade_dir(row["to"])
    return 0


def firm_calls_from_history(ticker: str, upgrades: pd.DataFrame, close: pd.Series, spy: pd.Series,
                            horizon: int = 60) -> list[dict]:
    """Score each past directional call: did the stock beat (or lag) the S&P 500 over `horizon` trading days?"""
    calls = []
    if upgrades.empty or close is None or close.dropna().empty:
        return calls
    close = close.dropna()
    spy = spy.reindex(close.index).ffill()
    idx = close.index
    for _, row in upgrades.iterrows():
        direction = call_direction(row)
        if direction == 0:
            continue
        pos = idx.searchsorted(pd.Timestamp(row["date"]))
        end = pos + horizon
        if pos >= len(idx) or end >= len(idx):
            continue
        ret = close.iloc[end] / close.iloc[pos] - 1
        sret = spy.iloc[end] / spy.iloc[pos] - 1 if not np.isnan(spy.iloc[pos]) else 0.0
        excess = float(ret - sret)
        calls.append({"firm": row["firm"], "ticker": ticker, "date": pd.Timestamp(row["date"]).date().isoformat(),
                      "direction": direction, "excess": excess, "hit": int(excess * direction > 0)})
    return calls


def firm_accuracy(calls: list[dict], prior_hits: float = 5, prior_n: float = 10) -> dict[str, dict]:
    agg: dict[str, list[int]] = {}
    for c in calls:
        agg.setdefault(c["firm"], []).append(int(c["hit"]))
    return {f: {"acc": (sum(h) + prior_hits) / (len(h) + prior_n), "n": len(h)} for f, h in agg.items()}


def ratings_signal(upgrades: pd.DataFrame, recs: pd.DataFrame | None, accuracy: dict, today: date) -> SignalResult:
    res = SignalResult("analyst_ratings")
    total, n_up, n_down, best = 0.0, 0, 0, None
    if not upgrades.empty:
        recent = upgrades[upgrades["date"] >= pd.Timestamp(today - timedelta(days=30))]
        for _, r in recent.iterrows():
            base = float(call_direction(r))
            if r["action"] == "init":
                base *= 0.5
            if base == 0 and not (math.isnan(r["pt_now"]) or math.isnan(r["pt_prior"])) and r["pt_prior"] > 0:
                ch = r["pt_now"] / r["pt_prior"] - 1
                base = 0.3 if ch > 0.02 else -0.3 if ch < -0.02 else 0.0
            if base == 0:
                continue
            fa = accuracy.get(r["firm"], {"acc": 0.5, "n": 0})
            w = clip(1 + (fa["acc"] - 0.5) * 6, 0.3, 2.0)
            age = max(0, (today - r["date"].date()).days)
            total += base * w * 0.5 ** (age / 20)
            if base > 0:
                n_up += 1
                if fa["n"] >= 5 and (best is None or fa["acc"] > best[1]):
                    best = (r["firm"], fa["acc"], fa["n"])
            else:
                n_down += 1
        if n_up or n_down:
            res.reasons.append(f"בחודש האחרון: {n_up} שדרוגים/העלאות יעד ו־{n_down} הורדות")
        if best and best[1] >= 0.55:
            res.reasons.append(f"שודרגה ע\"י {best[0]}, שצדקה ב־{pct(best[1])} מ־{best[2]} ההמלצות שנבדקו")
    rec_delta = None
    if isinstance(recs, pd.DataFrame) and not recs.empty and _col(recs, "period") is not None:
        pc = _col(recs, "period")
        r = recs.set_index(pc)

        def bull(p):
            if p not in r.index:
                return None
            row = r.loc[p]
            tot = sum(num(row.get(k), 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
            return (num(row.get("strongBuy"), 0) + num(row.get("buy"), 0)) / tot if tot else None
        now, before = bull("0m"), bull("-2m") or bull("-1m")
        if now is not None and before is not None:
            rec_delta = now - before
            total += clip(rec_delta / 0.15) * 0.6
    if not (n_up or n_down) and rec_delta is None:
        res.conf = 0.3
        return res
    res.score = clip(math.tanh(total / 2))
    res.conf = 0.8
    res.data = {"n_up": n_up, "n_down": n_down, "rec_delta": rec_delta}
    return res


def target_signal(price_targets: dict, info: dict, price: float) -> SignalResult:
    res = SignalResult("price_target")
    mean = num((price_targets or {}).get("mean")) or num(info.get("targetMeanPrice"))
    high = num((price_targets or {}).get("high")) or num(info.get("targetHighPrice"))
    n = num(info.get("numberOfAnalystOpinions"), 0) or 0
    if not mean or not price:
        return res
    up = mean / price - 1
    res.score = clip(up / 0.5)
    res.conf = min(1.0, max(n, 1) / 8) * 0.8
    res.reasons.append(f"מחיר היעד הממוצע של {int(n) or 'כמה'} אנליסטים: {mean:.2f}$ ({pct(up, sign=True)} מהמחיר)")
    if high and high > mean * 1.2:
        res.reasons.append(f"היעד הגבוה ביותר: {high:.2f}$ ({pct(high / price - 1, sign=True)})")
    res.data = {"upside": up, "n": n}
    return res
