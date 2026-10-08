"""Insider buying/selling (Form 4). Open-market purchases by executives are among the more reliable
public signals; planned (10b5-1) trades and routine sales carry little information."""
from __future__ import annotations

import math
import re
from datetime import date

import pandas as pd

from ..sources.sec import CEO_CFO
from ..util import SignalResult, clip, money_he


def yahoo_insider_rows(df: pd.DataFrame | None) -> list[dict]:
    """Convert yfinance Ticker.insider_transactions into the same row format as SEC Form 4 rows."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        text = str(r.get("Text", "") or r.get("Transaction", "") or "")
        if re.search(r"purchase|buy", text, re.I):
            code = "P"
        elif re.search(r"\bsale\b|\bsold\b|\bsell", text, re.I):
            code = "S"
        else:
            code = "?"
        shares = float(r.get("Shares") or 0)
        value = float(r.get("Value") or 0) if not pd.isna(r.get("Value")) else 0.0
        try:
            d = pd.Timestamp(r.get("Start Date")).date()
        except (ValueError, TypeError):
            d = None
        pos = str(r.get("Position", "") or "")
        rows.append({"date": d, "code": code, "shares": shares, "price": (value / shares) if shares else 0.0,
                     "ad": "A" if code == "P" else "D", "owner": str(r.get("Insider", "")), "title": pos,
                     "is_officer": bool(pos), "planned": False, "top_exec": bool(CEO_CFO.search(pos))})
    return rows


def insider_signal(rows: list[dict], market_cap: float | None, lookback_days: int, today: date,
                   source: str = "SEC") -> SignalResult:
    res = SignalResult("insider_buying")
    rows = [r for r in rows if r.get("date") is None or (today - r["date"]).days <= lookback_days]
    if not rows:
        res.conf = 0.4 if source else 0.0
        return res
    buy_value, buyers, top_buyers = 0.0, set(), set()
    sell_value, sellers = 0.0, set()
    for r in rows:
        value = (r.get("shares") or 0) * (r.get("price") or 0)
        age = (today - r["date"]).days if r.get("date") else lookback_days
        if r.get("code") == "P" and (r.get("ad") or "A") == "A":
            w = (1.0 if age <= 30 else 0.6) * (0.5 if r.get("planned") else 1.0)
            buy_value += value * w
            buyers.add(r.get("owner", ""))
            if r.get("top_exec"):
                top_buyers.add(r.get("title") or r.get("owner"))
        elif r.get("code") == "S" and not r.get("planned"):
            sell_value += value
            sellers.add(r.get("owner", ""))

    s = 0.0
    if buy_value > 0:
        s += clip(math.log10(max(buy_value, 1) / 25_000) / 2.3, 0, 1) * 0.6
        if market_cap:
            s += clip(buy_value / market_cap / 0.001, 0, 1) * 0.15
        n = len(buyers)
        s += 0.25 if n >= 3 else 0.15 if n >= 2 else 0.0
        if top_buyers:
            s += 0.15
        who = "בעל עניין אחד קנה" if len(buyers) == 1 else f"{len(buyers)} בעלי עניין קנו"
        txt = f"{who} מניות בשוק הפתוח בכ־{money_he(buy_value)} ב־{lookback_days} הימים האחרונים"
        if top_buyers:
            txt += f" ({sorted(top_buyers)[0]})" if len(buyers) == 1 else f" (כולל {sorted(top_buyers)[0]})"
        res.reasons.append(txt)
    if sell_value > 0 and market_cap and sell_value / market_cap > 0.005:
        s -= 0.3
        res.reasons.append(f"מכירות לא מתוכננות של בעלי עניין בכ־{money_he(sell_value)}")
    if len(sellers) >= 3 and buy_value == 0:
        s -= 0.2
        if not any("מכירות" in x for x in res.reasons):
            res.reasons.append(f"{len(sellers)} בעלי עניין מכרו מניות ולא נרשמו קניות")
    res.score = clip(s)
    res.conf = 0.9 if source == "SEC" else 0.7
    res.data = {"buy_value": buy_value, "n_buyers": len(buyers), "sell_value": sell_value, "source": source}
    return res
