"""Market data via yfinance (free, unofficial Yahoo Finance access).

Prices for the whole universe in batches, plus per-ticker details: company info, short interest,
analyst estimates/revisions/rating changes/price targets, news, earnings date, options activity
and Yahoo's insider-transaction table (used as a fallback if SEC is unreachable).
"""
from __future__ import annotations

import hashlib
import importlib
import logging
import pickle
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("radar.market")

FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def _safe(fn, default=None):
    try:
        val = fn()
    except Exception as e:  # noqa: BLE001
        log.debug("yfinance field failed: %s", e)
        return default
    if val is None:
        return default
    if isinstance(val, pd.DataFrame) and val.empty:
        return default
    return val


def normalize_news(items) -> list[dict]:
    """Handle both the old flat and the new nested ('content') yfinance news formats."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        c = it.get("content") if isinstance(it.get("content"), dict) else None
        if c:
            title = c.get("title")
            summary = c.get("summary") or c.get("description") or ""
            pub = c.get("pubDate") or c.get("displayTime")
            source = (c.get("provider") or {}).get("displayName", "")
            url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url", "")
            try:
                published = datetime.fromisoformat(str(pub).replace("Z", "+00:00")) if pub else None
            except ValueError:
                published = None
        else:
            title = it.get("title") or it.get("headline")
            summary = it.get("summary", "")
            source = it.get("publisher") or it.get("source", "")
            url = it.get("link") or it.get("url", "")
            ts = it.get("providerPublishTime") or it.get("datetime")
            published = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
        if not title:
            continue
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        out.append({"title": str(title), "summary": str(summary or ""), "published": published,
                    "source": str(source or ""), "url": str(url or "")})
    return out


def parse_calendar(cal) -> date | None:
    """Next earnings date from Ticker.calendar (dict in new versions, DataFrame in old)."""
    vals = None
    if isinstance(cal, dict):
        vals = cal.get("Earnings Date")
    elif isinstance(cal, pd.DataFrame) and not cal.empty:
        if "Earnings Date" in cal.index:
            vals = list(cal.loc["Earnings Date"].values)
        elif "Earnings Date" in cal.columns:
            vals = list(cal["Earnings Date"].values)
    if vals is None:
        return None
    if not isinstance(vals, (list, tuple, np.ndarray)):
        vals = [vals]
    dates = []
    for v in vals:
        try:
            dates.append(pd.Timestamp(v).date())
        except (ValueError, TypeError):
            continue
    today = date.today()
    future = [d for d in dates if d >= today]
    return min(future) if future else (max(dates) if dates else None)


def to_panel(df: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Convert yf.download output into {field: wide DataFrame (dates x tickers)}."""
    panel = {}
    if df is None or df.empty:
        return {f: pd.DataFrame() for f in FIELDS}
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = set(df.columns.get_level_values(0))
        for f in FIELDS:
            if f in lvl0:
                panel[f] = df[f].copy()
            else:  # group_by='ticker' layout: (ticker, field)
                try:
                    panel[f] = df.xs(f, axis=1, level=1).copy()
                except KeyError:
                    panel[f] = pd.DataFrame(index=df.index)
    else:
        for f in FIELDS:
            panel[f] = df[[f]].rename(columns={f: tickers[0]}) if f in df.columns else pd.DataFrame(index=df.index)
    for f in FIELDS:
        p = panel[f]
        p.index = pd.to_datetime(p.index).tz_localize(None) if getattr(p.index, "tz", None) else pd.to_datetime(p.index)
        panel[f] = p.sort_index()
    return panel


def merge_panels(panels: list[dict]) -> dict[str, pd.DataFrame]:
    out = {}
    for f in FIELDS:
        frames = [p[f] for p in panels if f in p and not p[f].empty]
        if frames:
            m = pd.concat(frames, axis=1)
            out[f] = m.loc[:, ~m.columns.duplicated()].sort_index()
        else:
            out[f] = pd.DataFrame()
    return out


class YahooMarket:
    def __init__(self, cache_dir: Path, yf_module=None, max_expirations: int = 2):
        self.yf = yf_module or importlib.import_module("yfinance")
        self.cache_dir = Path(cache_dir)
        self.max_expirations = max_expirations

    # ------------------------------------------------------------ prices
    def download_prices(self, tickers: list[str], period: str = "2y", chunk: int = 200,
                        use_cache: bool = True) -> dict[str, pd.DataFrame]:
        tickers = sorted(set(tickers))
        key = hashlib.sha1((",".join(tickers) + period).encode()).hexdigest()[:16]
        cpath = self.cache_dir / f"prices_{date.today():%Y%m%d}_{key}.pkl"
        if use_cache and cpath.exists():
            with open(cpath, "rb") as fh:
                return pickle.load(fh)
        panels = []
        for i in range(0, len(tickers), chunk):
            batch = tickers[i:i + chunk]
            log.info("מוריד מחירים %d-%d מתוך %d...", i + 1, i + len(batch), len(tickers))
            for attempt in range(3):
                try:
                    df = self.yf.download(batch, period=period, interval="1d", auto_adjust=True,
                                          group_by="column", threads=True, progress=False)
                    panels.append(to_panel(df, batch))
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("הורדת מחירים נכשלה (ניסיון %d): %s", attempt + 1, e)
                    time.sleep(5 * (attempt + 1))
        panel = merge_panels(panels)
        if use_cache and not panel["Close"].empty:
            with open(cpath, "wb") as fh:
                pickle.dump(panel, fh)
        return panel

    # ------------------------------------------------------------ details
    def _options_summary(self, t, price: float | None) -> dict | None:
        exps = _safe(lambda: list(t.options), [])
        if not exps:
            return None
        call_vol = put_vol = call_oi = put_oi = otm_call_vol = 0.0
        ivs = []
        for exp in exps[: self.max_expirations]:
            chain = _safe(lambda e=exp: t.option_chain(e))
            if chain is None:
                continue
            calls, puts = chain.calls, chain.puts
            call_vol += float(np.nansum(calls.get("volume", pd.Series(dtype=float)).values))
            put_vol += float(np.nansum(puts.get("volume", pd.Series(dtype=float)).values))
            call_oi += float(np.nansum(calls.get("openInterest", pd.Series(dtype=float)).values))
            put_oi += float(np.nansum(puts.get("openInterest", pd.Series(dtype=float)).values))
            if price and "strike" in calls:
                otm = calls[calls["strike"] > price * 1.05]
                otm_call_vol += float(np.nansum(otm.get("volume", pd.Series(dtype=float)).values))
                near = calls[(calls["strike"] >= price * 0.9) & (calls["strike"] <= price * 1.1)]
                if "impliedVolatility" in near:
                    ivs.extend([v for v in near["impliedVolatility"].values if v and v > 0])
        return {"call_vol": call_vol, "put_vol": put_vol, "call_oi": call_oi, "put_oi": put_oi,
                "otm_call_vol": otm_call_vol, "atm_iv": float(np.median(ivs)) if ivs else None}

    def details(self, ticker: str, price: float | None = None, want_options: bool = True) -> dict:
        t = self.yf.Ticker(ticker)
        info = {}
        for attempt in range(2):  # an empty info is usually Yahoo rate limiting: wait and retry once
            info = _safe(lambda: t.info, {}) or {}
            if len(info) > 3:
                break
            time.sleep(4 * (attempt + 1))
        d = {
            "info": info,
            "news": normalize_news(_safe(lambda: t.news, [])),
            "earnings_date": parse_calendar(_safe(lambda: t.calendar)),
            "recommendations": _safe(lambda: t.recommendations),
            "upgrades_downgrades": _safe(lambda: t.upgrades_downgrades),
            "eps_trend": _safe(lambda: t.eps_trend),
            "eps_revisions": _safe(lambda: t.eps_revisions),
            "price_targets": _safe(lambda: t.analyst_price_targets, {}) or {},
            "insider_yahoo": _safe(lambda: t.insider_transactions),
        }
        d["options"] = self._options_summary(t, price) if want_options else None
        return d
