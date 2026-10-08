"""Synthetic market for trying the system offline. Tickers are fake (DMO001...) and every number is
generated - nothing here describes a real company."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

INDUSTRIES = [
    ("Industrials", "Aerospace & Defense", "Builds missile defense systems and military electronics."),
    ("Energy", "Oil & Gas E&P", "Explores and produces crude oil and natural gas."),
    ("Technology", "Semiconductors", "Designs semiconductors for data centers."),
    ("Technology", "Software - Infrastructure", "Provides cybersecurity and threat detection software."),
    ("Healthcare", "Biotechnology", "Develops oncology drug candidates in clinical trials."),
    ("Industrials", "Marine Shipping", "Operates a fleet of crude oil tankers."),
    ("Basic Materials", "Gold", "Mines gold in North America."),
    ("Basic Materials", "Agricultural Inputs", "Produces potash and nitrogen fertilizer."),
    ("Industrials", "Airlines", "Operates passenger airline routes."),
    ("Consumer Cyclical", "Specialty Retail", "Sells imported consumer goods through retail stores."),
    ("Financial Services", "Banks - Regional", "Regional commercial bank."),
    ("Communication Services", "Internet Content & Information", "Runs online platforms."),
]
COUNTRIES = ["United States"] * 14 + ["Israel", "China", "Canada", "Brazil", "Cayman Islands"]
FIRMS = ["Demo Securities", "Sample Capital", "Example Partners", "Placeholder Research", "Model Markets"]
FIRM_SKILL = {"Demo Securities": 0.8, "Sample Capital": 0.5, "Example Partners": 0.2,
              "Placeholder Research": 0.0, "Model Markets": -0.3}
GOOD_NEWS = ["{n} raises full-year guidance after strong demand", "{n} beats estimates on record revenue",
             "{n} wins contract from U.S. Army", "{n} announces share repurchase program",
             "{n} reports positive topline results in Phase 3 trial"]
BAD_NEWS = ["{n} announces $50 million public offering", "{n} cuts guidance as weak demand persists",
            "{n} misses estimates, shares slump", "{n} faces class action lawsuit"]
NEUTRAL_NEWS = ["{n} to present at industry conference", "{n} names new board member"]


class DemoSources:
    is_demo = True

    def __init__(self, settings=None, n: int = 260, years: int = 6, seed: int = 11, asof: date | None = None):
        self.rng = np.random.default_rng(seed)
        rng = self.rng
        real_today = date.today()
        idx = pd.bdate_range(end=pd.Timestamp(real_today) - pd.Timedelta(days=1), periods=252 * years)
        self.index = idx
        # "asof" lets the demo replay a past day (used to build a realistic demo history)
        self.today = asof or real_today
        self.cut = int(idx.searchsorted(pd.Timestamp(self.today)))  # bars strictly before asof are known
        T = len(idx)
        self.tickers = [f"DMO{i:03d}" for i in range(1, n + 1)]
        mkt = rng.normal(0.0004, 0.010, T)
        close, high, low, opn, vol = {}, {}, {}, {}, {}
        self.mu, self.meta = {}, {}
        for i, t in enumerate(self.tickers):
            beta = rng.uniform(0.5, 1.8)
            sig = rng.uniform(0.012, 0.045)
            mu = np.zeros(T)
            for k in range(1, T):  # persistent drift = momentum exists in this toy market
                mu[k] = 0.98 * mu[k - 1] + rng.normal(0, 0.0003)
            jumps = (rng.random(T) < 1 / 300) * rng.normal(0.02, 0.15, T)
            r = beta * mkt + mu + rng.normal(0, sig, T) + jumps
            r = np.clip(r, -0.6, 0.8)
            c = rng.uniform(5, 120) * np.exp(np.cumsum(r))
            o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, sig / 3, T))
            hi = np.maximum(c, o) * (1 + np.abs(rng.normal(0, sig / 2, T)))
            lo = np.minimum(c, o) * (1 - np.abs(rng.normal(0, sig / 2, T)))
            base_vol = rng.uniform(2e5, 6e6)
            v = base_vol * np.exp(rng.normal(0, 0.3, T)) * (1 + 3 * np.abs(r) / sig * 0.3)
            close[t], high[t], low[t], opn[t], vol[t] = c, hi, lo, o, v
            self.mu[t] = mu
            sector, industry, summary = INDUSTRIES[i % len(INDUSTRIES)]
            self.meta[t] = {"sector": sector, "industry": industry, "summary": summary,
                            "country": COUNTRIES[int(rng.integers(0, len(COUNTRIES)))],
                            "shares": rng.uniform(2e7, 4e8), "beta": beta}
        spy = 400 * np.exp(np.cumsum(mkt))
        close["SPY"], opn["SPY"] = spy, np.r_[spy[0], spy[:-1]]
        high["SPY"], low["SPY"], vol["SPY"] = spy * 1.004, spy * 0.996, np.full(T, 8e7)
        mk = lambda d: pd.DataFrame(d, index=idx)  # noqa: E731
        self.panel = {"Open": mk(opn), "High": mk(high), "Low": mk(low), "Close": mk(close), "Volume": mk(vol)}

    # ---------------------------------------------------------------- universe & prices
    def universe(self):
        return [{"ticker": t, "cik": 1000 + i, "name": f"Demo Company {t[3:]}", "exchange": "Nasdaq"}
                for i, t in enumerate(self.tickers)]

    def cik_lookup(self):
        return {u["ticker"]: u for u in self.universe()}

    def prices(self, tickers, period="2y", use_cache=True):
        years = int(str(period).rstrip("y")) if str(period).endswith("y") else 2
        end = self.index[self.cut - 1]
        start = end - pd.DateOffset(years=years)
        cols = [t for t in tickers if t in self.panel["Close"].columns]
        return {f: df.loc[(df.index >= start) & (df.index <= end), cols] for f, df in self.panel.items()}

    # ---------------------------------------------------------------- details
    def _quality(self, t: str) -> float:
        mu = self.mu.get(t)
        return 0.0 if mu is None else float(np.clip(mu[max(0, self.cut - 20):self.cut].mean() / 0.002, -1, 1))

    def details(self, ticker, price=None, want_options=True):
        rng = np.random.default_rng(int(hashlib.md5(f"{ticker}{self.today}".encode()).hexdigest()[:8], 16))
        m, q = self.meta[ticker], self._quality(ticker)
        price = price or float(self.panel["Close"][ticker].iloc[self.cut - 1])
        name = f"Demo Company {ticker[3:]}"
        spf = float(np.clip(rng.gamma(1.5, 0.05), 0.005, 0.45))
        info = {"longName": name, "sector": m["sector"], "industry": m["industry"], "country": m["country"],
                "longBusinessSummary": m["summary"], "marketCap": price * m["shares"],
                "shortPercentOfFloat": spf, "shortRatio": float(rng.uniform(0.5, 9)),
                "sharesShort": m["shares"] * spf, "sharesShortPriorMonth": m["shares"] * spf * rng.uniform(0.7, 1.2),
                "floatShares": m["shares"], "numberOfAnalystOpinions": int(rng.integers(2, 20)),
                "targetMeanPrice": price * (1 + 0.15 + 0.3 * q + rng.normal(0, 0.1)),
                "targetHighPrice": price * (1.5 + 0.4 * max(q, 0))}
        eps = abs(rng.normal(2, 1)) + 0.1
        rev = 0.12 * q + rng.normal(0, 0.03)
        trend = pd.DataFrame({"current": [eps / 4, eps / 4, eps, eps * 1.15]}, index=["0q", "+1q", "0y", "+1y"])
        trend["7daysAgo"] = trend["current"] / (1 + rev / 4)
        trend["30daysAgo"] = trend["current"] / (1 + rev)
        trend["60daysAgo"] = trend["current"] / (1 + rev * 1.4)
        trend["90daysAgo"] = trend["current"] / (1 + rev * 1.7)
        up = int(rng.poisson(max(0.2, 3 + 6 * q)))
        down = int(rng.poisson(max(0.2, 3 - 2.5 * q)))
        revisions = pd.DataFrame({"upLast7days": [up // 3] * 4, "upLast30days": [up] * 4,
                                  "downLast30days": [down] * 4, "downLast7Days": [down // 3] * 4},
                                 index=["0q", "+1q", "0y", "+1y"])
        # rating history: skilled firms call the (future) direction more often
        rows, closes = [], self.panel["Close"][ticker]
        for _ in range(int(rng.integers(6, 16))):
            pos = int(rng.integers(30, max(31, self.cut - 62)))
            fwd = closes.iloc[pos + 60] / closes.iloc[pos] - 1
            firm = FIRMS[int(rng.integers(0, len(FIRMS)))]
            right = rng.random() < 0.5 + 0.4 * FIRM_SKILL[firm]
            direction = (1 if fwd > 0 else -1) * (1 if right else -1)
            rows.append({"GradeDate": self.index[pos], "Firm": firm,
                         "ToGrade": "Buy" if direction > 0 else "Sell", "FromGrade": "Hold",
                         "Action": "up" if direction > 0 else "down"})
        if q > 0.3:
            rows.append({"GradeDate": pd.Timestamp(self.today - timedelta(days=int(rng.integers(1, 20)))),
                         "Firm": "Demo Securities", "ToGrade": "Buy", "FromGrade": "Hold", "Action": "up"})
        ud = pd.DataFrame(rows).set_index("GradeDate").sort_index(ascending=False)
        recs = pd.DataFrame({"period": ["0m", "-1m", "-2m", "-3m"],
                             "strongBuy": [int(4 + 3 * q), 4, 4, 4], "buy": [6, 6, 5, 5],
                             "hold": [5, 5, 6, 6], "sell": [1, 1, 1, 1], "strongSell": [0, 0, 0, 0]})
        now = datetime.combine(self.today, datetime.min.time(), tzinfo=timezone.utc)
        news = []
        for _ in range(int(rng.integers(1, 6))):
            pool = GOOD_NEWS if rng.random() < 0.5 + 0.4 * q else BAD_NEWS if rng.random() < 0.6 else NEUTRAL_NEWS
            if m["industry"] != "Biotechnology":
                pool = [p for p in pool if "Phase" not in p] or NEUTRAL_NEWS
            news.append({"title": pool[int(rng.integers(0, len(pool)))].format(n=name), "summary": "",
                         "published": now - timedelta(hours=float(rng.uniform(2, 24 * 9))),
                         "source": "Demo Wire", "url": ""})
        base_oi = rng.uniform(2e3, 8e4)
        cv = base_oi * rng.uniform(0.05, 0.4) * (1 + 2 * max(q, 0))
        pv = base_oi * rng.uniform(0.05, 0.3) * (1 + max(-q, 0))
        options = {"call_vol": cv, "put_vol": pv, "call_oi": base_oi, "put_oi": base_oi * 0.8,
                   "otm_call_vol": cv * rng.uniform(0.2, 0.7), "atm_iv": float(rng.uniform(0.3, 1.3))} if want_options else None
        return {"info": info, "news": news,
                "earnings_date": self.today + timedelta(days=int(rng.integers(1, 80))),
                "recommendations": recs, "upgrades_downgrades": ud, "eps_trend": trend, "eps_revisions": revisions,
                "price_targets": {"mean": info["targetMeanPrice"], "high": info["targetHighPrice"]},
                "insider_yahoo": None, "options": options}

    # ---------------------------------------------------------------- insiders
    def insider(self, cik, lookback_days):
        t = self.tickers[cik - 1000]
        rng = np.random.default_rng(cik)
        q, price = self._quality(t), float(self.panel["Close"][t].iloc[self.cut - 1])
        filings = []
        if rng.random() < 0.15 + 0.5 * max(q, 0):
            for k in range(int(rng.integers(1, 4))):
                filings.append({"owner": f"Insider {k + 1}", "title": ["CEO", "CFO", "Director"][k % 3],
                                "is_officer": k < 2, "is_director": k == 2, "is_ten_pct": False, "planned": False,
                                "transactions": [{"date": self.today - timedelta(days=int(rng.integers(2, 60))),
                                                  "code": "P", "shares": float(rng.integers(5_000, 80_000)),
                                                  "price": price * 0.95, "ad": "A"}]})
        if rng.random() < 0.4:
            filings.append({"owner": "Insider 9", "title": "EVP", "is_officer": True, "is_director": False,
                            "is_ten_pct": False, "planned": True,
                            "transactions": [{"date": self.today - timedelta(days=int(rng.integers(2, 80))),
                                              "code": "S", "shares": 20_000.0, "price": price, "ad": "D"}]})
        return filings

    def insider_history(self, years, ciks=None, log_fn=None):
        """Synthetic insider trades over the whole demo history. Buys are more likely while a stock's hidden
        drift is positive, so the training code has something real to find."""
        rows = []
        close = self.panel["Close"]
        for i, t in enumerate(self.tickers):
            cik = 1000 + i
            if ciks is not None and cik not in ciks:
                continue
            rng = np.random.default_rng(cik * 7 + 3)
            mu = self.mu[t]
            for p in range(5, self.cut, 5):
                q = float(np.clip(mu[p] / 0.002, -1, 1))
                d = self.index[p].date()
                if rng.random() < 0.004 + 0.05 * max(q, 0):
                    n = 1 + int(rng.random() < 0.3 + 0.4 * max(q, 0))
                    for k in range(n):
                        top = k == 0 and rng.random() < 0.5
                        sh = float(rng.integers(2_000, 60_000))
                        rows.append({"cik": cik, "filing_date": d, "date": d - timedelta(days=2), "code": "P",
                                     "shares": sh, "price": float(close[t].iloc[p]), "ad": "A",
                                     "shares_after": sh * float(rng.uniform(1.5, 12)), "owner": f"Insider {k + 1}",
                                     "title": "CEO" if top else "Director", "is_officer": top, "is_director": not top,
                                     "is_ten_pct": False, "planned": False, "top_exec": top})
                if rng.random() < 0.02:
                    rows.append({"cik": cik, "filing_date": d, "date": d - timedelta(days=2), "code": "S",
                                 "shares": 20_000.0, "price": float(close[t].iloc[p]), "ad": "D",
                                 "shares_after": 50_000.0, "owner": "Insider 9", "title": "EVP", "is_officer": True,
                                 "is_director": False, "is_ten_pct": False, "planned": rng.random() < 0.6,
                                 "top_exec": False})
        if log_fn:
            log_fn("demo", len(rows))
        return pd.DataFrame(rows)

    def earnings_events(self, ticker, cik, details=None, since=None, yahoo=True, http=None):
        """Synthetic quarterly reports every ~63 trading days; the surprise follows the hidden drift."""
        if ticker not in self.mu:
            return None
        i = self.tickers.index(ticker)
        rng = np.random.default_rng(i * 13 + 5)
        mu = self.mu[ticker]
        since = since or self.today - timedelta(days=200)
        rows = []
        for p in range(10 + i % 63, self.cut, 63):
            d = self.index[p].date()
            if d < since:
                continue
            sur = float(np.clip(mu[p] / 0.002 * 0.15 + rng.normal(0.02, 0.08), -1, 1))
            rows.append({"date": d, "surprise": sur if rng.random() < 0.8 else np.nan})
        return pd.DataFrame(rows, columns=["date", "surprise"])

    def insider_discovery(self, days, max_filings):
        best = sorted(self.tickers, key=lambda t: -self._quality(t))[:3]
        return {t: {"value": 750_000.0, "n_insiders": 2, "filings": []} for t in best}

    # ---------------------------------------------------------------- context
    def gov_contracts(self, name, days):
        t = "DMO" + name.split()[-1]
        if self.meta.get(t, {}).get("industry") == "Aerospace & Defense":
            return {"total": float(self.rng.uniform(5e6, 3e8)), "count": 4, "agencies": ["Department of Defense"]}
        return None

    def macro_series(self):
        idx_d = pd.bdate_range(end=self.index[-1], periods=900)
        idx_m = pd.date_range(end=self.index[-1], periods=40, freq="MS")
        lin = lambda a, b, n: np.linspace(a, b, n)  # noqa: E731
        return {
            "DGS10": pd.Series(lin(4.6, 4.1, 900), index=idx_d), "DGS2": pd.Series(lin(4.3, 3.6, 900), index=idx_d),
            "DFF": pd.Series(lin(5.3, 3.9, 900), index=idx_d), "T10Y2Y": pd.Series(lin(-0.3, 0.5, 900), index=idx_d),
            "VIXCLS": pd.Series(lin(19, 16.5, 900), index=idx_d),
            "BAMLH0A0HYM2": pd.Series(lin(3.6, 3.1, 900), index=idx_d),
            "CPIAUCSL": pd.Series(300 * 1.0025 ** np.arange(40), index=idx_m),
            "UNRATE": pd.Series(lin(3.9, 4.2, 40), index=idx_m),
        }

    def themes(self):
        from .gdelt import THEMES
        demo = {"middle_east": 1.65, "shipping_lanes": 1.4, "russia_ukraine": 0.95, "global_conflict": 1.2,
                "taiwan_china": 1.05, "cyber": 1.3, "tariffs": 0.8}
        out = {}
        for k, ratio in demo.items():
            out[k] = {"recent": ratio, "base": 1.0, "ratio": ratio, "escalation": float(np.clip((ratio - 1) / 0.75, -1, 1)),
                      "last_date": self.today.strftime("%Y%m%d"), "he": THEMES[k]["he"], "tilts": THEMES[k]["tilts"]}
        return out

    def country_stats(self, iso):
        demo = {"ISR": (3.1, 3.6, 3.0), "CHN": (4.6, 4.2, 0.4), "CAN": (1.4, 1.9, 2.2), "BRA": (2.2, 2.0, 4.8)}
        if iso not in demo:
            return None
        g, gn, inf = demo[iso]
        return {"iso": iso, "growth_now": g, "growth_next": gn, "inflation": inf, "source": "דמו"}

    def country_conflict(self, name):
        return {"escalation": 0.5, "ratio": 1.4} if name == "Israel" else None

    def extra_news(self, ticker, days):
        return []

    def classify_news(self, ticker, name, news):
        return None
