"""Point-in-time features shared by model training (history) and the daily scan (today).

The SAME functions compute the features in both places, so the model is used exactly as it was tested.
Every feature at date t only uses information that was public at t (insider data by SEC filing date,
earnings by announcement date, prices up to t's close)."""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

TECH = ["mom_6_1", "rs_63", "ret_21", "log_vol_ratio", "dist_high", "above_50", "trend_50_200", "squeeze",
        "atr_pct", "rsi14", "log_dollar_vol", "log_price"]
INSIDER = ["ins_buy", "ins_log_value", "ins_n_buyers", "ins_top_exec", "ins_hold_incr", "ins_net_sell"]
EARN = ["earn_recent", "earn_surprise", "earn_reaction", "earn_age"]
MARKET = ["mkt_above_200", "mkt_ret_63"]
FEATURES = TECH + INSIDER + EARN + MARKET

LABELS_HE = {
    "mom_6_1": "מומנטום חצי שנה", "rs_63": "חוזק מול S&P 500", "ret_21": "תשואת החודש האחרון",
    "log_vol_ratio": "מחזור מסחר חריג", "dist_high": "קרבה לשיא השנתי", "above_50": "מעל ממוצע 50 ימים",
    "trend_50_200": "מגמה עולה (50 מעל 200)", "squeeze": "התכווצות תנודה", "atr_pct": "תנודתיות יומית",
    "rsi14": "RSI", "log_dollar_vol": "נזילות", "log_price": "מחיר המניה",
    "ins_buy": "קניות מנהלים", "ins_log_value": "סכום קניות המנהלים", "ins_n_buyers": "מספר מנהלים שקנו",
    "ins_top_exec": "מנכ\"ל או סמנכ\"ל כספים קנה", "ins_hold_incr": "הגדלת ההחזקה של המנהל",
    "ins_net_sell": "מכירות כבדות של מנהלים", "earn_recent": "דוח רבעוני לאחרונה",
    "earn_surprise": "הפתעה בדוח הרבעוני", "earn_reaction": "קפיצה ביום הדוח", "earn_age": "זמן מאז הדוח",
    "mkt_above_200": "שוק במגמה עולה", "mkt_ret_63": "תשואת השוק ב־3 חודשים",
}


def _f(x, default=np.nan) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) or math.isinf(v) else v


# ------------------------------------------------------------------ technical
def tech_features(r) -> dict:
    """r: mapping with the columns of signals.technical.compute_panel_features at one date."""
    g = (lambda k: _f(r.get(k))) if isinstance(r, dict) else (lambda k: _f(r[k]) if k in r else np.nan)
    close, s50, s200, vr, dv = g("close"), g("sma50"), g("sma200"), g("vol_ratio"), g("dollar_vol20")
    rsi = g("rsi14")
    return {
        "mom_6_1": g("mom_6_1"), "rs_63": g("rs_63"), "ret_21": g("ret_21"),
        "log_vol_ratio": math.log(vr) if vr and vr > 0 else np.nan,
        "dist_high": g("dist_high"),
        "above_50": float(close > s50) if close == close and s50 == s50 else np.nan,
        "trend_50_200": float(s50 > s200) if s50 == s50 and s200 == s200 else np.nan,
        "squeeze": g("squeeze"), "atr_pct": g("atr_pct"),
        "rsi14": rsi / 100 if rsi == rsi else np.nan,
        "log_dollar_vol": math.log10(dv) if dv and dv > 0 else np.nan,
        "log_price": math.log10(close) if close and close > 0 else np.nan,
    }


def tech_frame(f: dict[str, pd.DataFrame], t) -> pd.DataFrame:
    """Vectorised technical features for all tickers at date t (same formulas as tech_features)."""
    g = {k: f[k].loc[t] for k in ("close", "sma50", "sma200", "vol_ratio", "dollar_vol20", "rsi14", "mom_6_1", "rs_63",
                                   "ret_21", "dist_high", "squeeze", "atr_pct")}
    with np.errstate(divide="ignore", invalid="ignore"):
        out = pd.DataFrame({
            "mom_6_1": g["mom_6_1"], "rs_63": g["rs_63"], "ret_21": g["ret_21"],
            "log_vol_ratio": np.log(g["vol_ratio"].where(g["vol_ratio"] > 0)),
            "dist_high": g["dist_high"],
            "above_50": (g["close"] > g["sma50"]).astype(float).where(g["sma50"].notna()),
            "trend_50_200": (g["sma50"] > g["sma200"]).astype(float).where(g["sma200"].notna()),
            "squeeze": g["squeeze"], "atr_pct": g["atr_pct"], "rsi14": g["rsi14"] / 100,
            "log_dollar_vol": np.log10(g["dollar_vol20"].where(g["dollar_vol20"] > 0)),
            "log_price": np.log10(g["close"].where(g["close"] > 0)),
        })
    return out


# ------------------------------------------------------------------ insiders
def _day(d) -> int | None:
    if d is None or (isinstance(d, float) and math.isnan(d)):
        return None
    if isinstance(d, pd.Timestamp):
        d = d.date()
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return None
    try:
        return d.toordinal()
    except AttributeError:
        return None


class InsiderArrays:
    """One company's open-market insider trades as sorted arrays (fast windows for many dates)."""

    def __init__(self, rows):
        df = pd.DataFrame(list(rows) if not isinstance(rows, pd.DataFrame) else rows)
        if df.empty:
            self.day = np.array([], dtype=int)
            return
        fd = df["filing_date"] if "filing_date" in df else df.get("date")
        if "date" in df:
            fd = fd.where(fd.notna(), df["date"])
        day = np.array([_day(x) or -1 for x in fd], dtype=int)
        keep = day > 0
        df, day = df[keep].reset_index(drop=True), day[keep]
        order = np.argsort(day, kind="stable")
        df, self.day = df.iloc[order].reset_index(drop=True), day[order]
        code = df["code"].astype(str).str.strip()
        ad = df["ad"].astype(str).str.strip() if "ad" in df else pd.Series(["A"] * len(df))
        ten = df["is_ten_pct"].fillna(False).astype(bool) if "is_ten_pct" in df else pd.Series([False] * len(df))
        off = df["is_officer"].fillna(False).astype(bool) if "is_officer" in df else pd.Series([False] * len(df))
        dire = df["is_director"].fillna(False).astype(bool) if "is_director" in df else pd.Series([False] * len(df))
        fund_only = (ten & ~off & ~dire).to_numpy()   # 10% holders that are not insiders (often funds)
        value = (pd.to_numeric(df["shares"], errors="coerce").fillna(0) * pd.to_numeric(df["price"], errors="coerce").fillna(0)).to_numpy()
        planned = df["planned"].fillna(False).astype(bool).to_numpy() if "planned" in df else np.zeros(len(df), bool)
        self.is_buy = ((code == "P") & (ad != "D")).to_numpy() & ~fund_only
        self.is_sell = ((code == "S") & (ad != "A")).to_numpy() & ~planned
        self.value = value
        self.top = df["top_exec"].fillna(False).astype(bool).to_numpy() if "top_exec" in df else np.zeros(len(df), bool)
        owners = df["owner"].astype(str).fillna("") if "owner" in df else pd.Series([""] * len(df))
        self.owner = pd.factorize(owners)[0]
        shares = pd.to_numeric(df["shares"], errors="coerce").to_numpy(dtype=float)
        after = pd.to_numeric(df["shares_after"], errors="coerce").to_numpy(dtype=float) if "shares_after" in df \
            else np.full(len(df), np.nan)
        before = after - shares
        with np.errstate(divide="ignore", invalid="ignore"):
            incr = np.where(before > 0, shares / before, np.where(after > 0, 2.0, np.nan))
        self.incr = np.clip(incr, 0, 2.0)

    def at(self, day: int, window: int = 90) -> dict:
        out = {"ins_buy": 0.0, "ins_log_value": 0.0, "ins_n_buyers": 0.0, "ins_top_exec": 0.0,
               "ins_hold_incr": 0.0, "ins_net_sell": 0.0}
        if self.day.size == 0:
            return out
        lo, hi = np.searchsorted(self.day, day - window, "right"), np.searchsorted(self.day, day, "right")
        if hi <= lo:
            return out
        b = self.is_buy[lo:hi]
        s = self.is_sell[lo:hi]
        bv = float(self.value[lo:hi][b].sum())
        sv = float(self.value[lo:hi][s].sum())
        if bv > 0:
            out["ins_buy"] = 1.0
            out["ins_log_value"] = math.log10(1 + bv / 1e4)
            out["ins_n_buyers"] = float(min(5, len(set(self.owner[lo:hi][b]))))
            out["ins_top_exec"] = float(self.top[lo:hi][b].any())
            inc = self.incr[lo:hi][b]
            inc = inc[~np.isnan(inc)]
            out["ins_hold_incr"] = float(np.median(inc)) if inc.size else 0.0
        n_sellers = len(set(self.owner[lo:hi][s]))
        if sv > 1e6 and sv > 3 * bv and n_sellers >= 2:
            out["ins_net_sell"] = 1.0
        return out


# ------------------------------------------------------------------ earnings
def merge_earnings(sec_dates, yahoo: pd.DataFrame | None, tolerance_days: int = 4) -> pd.DataFrame | None:
    """Quarterly report dates (SEC 8-K item 2.02 filings and/or Yahoo) with the EPS surprise when Yahoo has it.
    Returns DataFrame[date, surprise], or None when neither source knows anything about the company."""
    if sec_dates is None and (yahoo is None or len(yahoo) == 0):
        return None
    rows = [{"date": d, "surprise": np.nan} for d in (sec_dates or [])]
    y = [] if yahoo is None else [(r["date"], r["surprise"]) for _, r in yahoo.iterrows() if r["date"] is not None]
    for yd, ys in y:
        match = [r for r in rows if abs((r["date"] - yd).days) <= tolerance_days]
        if match:
            for r in match:
                if r["surprise"] != r["surprise"]:
                    r["surprise"] = ys
        else:
            rows.append({"date": yd, "surprise": ys})
    df = pd.DataFrame(rows, columns=["date", "surprise"])
    return df.sort_values("date").reset_index(drop=True)


def earnings_matrix(events: pd.DataFrame | None, close: pd.Series, spy: pd.Series | None, t_pos,
                    horizon: int = 63) -> dict[str, np.ndarray]:
    """Features from the latest earnings report that was fully priced in by each trading-day position in t_pos.
    close: one stock's closes (no gaps); spy: S&P 500 closes aligned to the same index (or None).
    The reaction is the 2-day move around the report (catches both before-open and after-close reports)
    minus the market's move. events=None means 'unknown' (NaN); an empty frame means 'no recent report'."""
    t_pos = np.atleast_1d(np.asarray(t_pos, dtype=int))
    n = len(t_pos)
    if events is None:
        return {k: np.full(n, np.nan) for k in EARN}
    out = {"earn_recent": np.zeros(n), "earn_surprise": np.zeros(n), "earn_reaction": np.zeros(n),
           "earn_age": np.ones(n)}
    if len(events) == 0 or len(close) < 3:
        return out
    ev = events[events["date"].notna()].sort_values("date")
    idx = close.index
    ks = idx.searchsorted(pd.to_datetime(ev["date"]).values)  # first trading day on/after the report date
    sur = pd.to_numeric(ev["surprise"], errors="coerce").to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    valid = (ks >= 1) & (ks + 1 < len(c))
    ks, sur = ks[valid], sur[valid]
    if ks.size == 0:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        react = c[ks + 1] / c[ks - 1] - 1
        if spy is not None:
            sp = spy.reindex(idx).to_numpy(dtype=float)
            mk = sp[ks + 1] / sp[ks - 1] - 1
            react = np.where(np.isnan(mk), react, react - mk)
    avail = ks + 1                                   # first day the full reaction is known
    j = np.searchsorted(avail, t_pos, side="right") - 1
    has = j >= 0
    jj = np.where(has, j, 0)
    age = t_pos - avail[jj]
    ok = has & (age <= horizon)
    r = np.clip(np.nan_to_num(react[jj], nan=0.0), -0.5, 0.5)
    out["earn_recent"] = ok.astype(float)
    out["earn_surprise"] = np.where(ok, np.clip(sur[jj], -1, 1), 0.0)   # NaN = report known, surprise unknown
    out["earn_reaction"] = np.where(ok, r, 0.0)
    out["earn_age"] = np.where(ok, age / horizon, 1.0)
    return out


def earnings_at(events: pd.DataFrame | None, close: pd.Series, spy: pd.Series | None, t_pos: int,
                horizon: int = 63) -> dict:
    m = earnings_matrix(events, close, spy, [t_pos], horizon)
    return {k: float(v[0]) for k, v in m.items()}


# ------------------------------------------------------------------ market
def market_at(spy: pd.Series | None, t_pos: int) -> dict:
    if spy is None or t_pos < 200:
        return {"mkt_above_200": np.nan, "mkt_ret_63": np.nan}
    s = spy.to_numpy(dtype=float)
    window = s[max(0, t_pos - 199):t_pos + 1]
    sma = np.nanmean(window)
    return {"mkt_above_200": float(s[t_pos] > sma), "mkt_ret_63": float(s[t_pos] / s[t_pos - 63] - 1)}


def live_features(tech: dict, insider_rows: list[dict], events: pd.DataFrame | None, close: pd.Series,
                  spy: pd.Series | None, asof: date) -> dict:
    """All model features for one stock today (same definitions as in training)."""
    feats = tech_features(tech)
    feats.update(InsiderArrays(insider_rows).at(asof.toordinal()))
    close = close.dropna()
    spy_full = spy.dropna() if spy is not None else None
    spy_al = spy_full.reindex(close.index).ffill() if spy_full is not None else None
    feats.update(earnings_at(events, close, spy_al, len(close) - 1))
    feats.update(market_at(spy_full, len(spy_full) - 1) if spy_full is not None and len(spy_full)
                 else {"mkt_above_200": np.nan, "mkt_ret_63": np.nan})
    return feats
