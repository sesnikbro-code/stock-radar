"""SQLite journal: every scored stock is logged, then checked after 30/90 days. This gives
(1) an honest track record, (2) a paper-trading portfolio and (3) data for learning which signals work.
The self-review (review.py) keeps its predictions, outcomes and log in the same file."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .util import clip

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (run_date TEXT PRIMARY KEY, regime REAL, label TEXT, n_candidates INT, n_picks INT);
CREATE TABLE IF NOT EXISTS candidates (
    run_date TEXT, ticker TEXT, price REAL, upside REAL, risk REAL, coverage REAL, pick INT,
    signals TEXT, reasons TEXT, PRIMARY KEY (run_date, ticker));
CREATE TABLE IF NOT EXISTS evaluations (
    run_date TEXT, ticker TEXT, horizon INT, ret REAL, spy_ret REAL, excess REAL, max_gain REAL,
    PRIMARY KEY (run_date, ticker, horizon));
CREATE TABLE IF NOT EXISTS paper (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, entry_date TEXT, entry_price REAL, initial_stop REAL,
    stop REAL, atr REAL, shares INT, status TEXT, exit_date TEXT, exit_price REAL, exit_reason TEXT,
    last_price REAL, plan TEXT);
CREATE TABLE IF NOT EXISTS firm_calls (
    firm TEXT, ticker TEXT, date TEXT, direction INT, excess REAL, hit INT,
    PRIMARY KEY (firm, ticker, date, direction));
CREATE TABLE IF NOT EXISTS options_hist (ticker TEXT, date TEXT, total_vol REAL, PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS weights (signal TEXT PRIMARY KEY, factor REAL, ic REAL, n INT, updated TEXT);
CREATE TABLE IF NOT EXISTS predictions (
    run_date TEXT, ticker TEXT, bar_date TEXT, pick INT, why_not TEXT, price REAL, atr REAL, cost REAL,
    prob REAL, exp_ret REAL, bucket INT, model TEXT, upside REAL, risk REAL, sector TEXT, earnings_date TEXT,
    risk_off INT, ext50 REAL, feats TEXT, signals TEXT, pros TEXT, plan TEXT, tg INT, PRIMARY KEY (run_date, ticker));
CREATE TABLE IF NOT EXISTS outcomes (
    run_date TEXT, ticker TEXT, status TEXT, entry_date TEXT, entry REAL, stop REAL, target REAL,
    exit_date TEXT, exit REAL, kind INT, ret REAL, r REAL, days INT, spy REAL, mfe_r REAL,
    ret21 REAL, spy21 REAL, PRIMARY KEY (run_date, ticker));
CREATE TABLE IF NOT EXISTS review_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, kind TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

HORIZONS = (30, 90)
# columns added after a table was first created (older journals get them on open)
ADDED_COLUMNS = [("predictions", "plan", "TEXT"), ("paper", "plan", "TEXT"), ("predictions", "tg", "INT")]


def plan_text(plan: dict | None) -> str | None:
    """Compact form of an exit plan: stop|target|days|trailing (empty = none)."""
    if not plan:
        return None
    f = lambda v: "" if v in (None, 0) else f"{float(v):g}"  # noqa: E731
    return "|".join([f(plan.get("stop_atr")), f(plan.get("target_r")), str(int(plan.get("window") or 63)),
                     f(plan.get("trail_atr"))])


def plan_from_text(s: str | None, default: dict | None = None) -> dict | None:
    if not s:
        return default
    try:
        a, b, c, d = (s.split("|") + ["", "", "", ""])[:4]
        return {"stop_atr": float(a), "target_r": float(b) if b else None, "window": int(c or 63),
                "trail_atr": float(d) if d else None}
    except ValueError:
        return default


def spearman(x: pd.Series, y: pd.Series) -> float:
    """Rank correlation without SciPy (Pearson correlation of the ranks)."""
    rx, ry = x.rank(), y.rank()
    if rx.std() == 0 or ry.std() == 0:
        return 0.0
    v = float(rx.corr(ry))
    return 0.0 if np.isnan(v) else v


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        for table, col, typ in ADDED_COLUMNS:
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ------------------------------------------------------------ runs
    def record_run(self, run_date: date, regime: dict, results: list[dict], n_picks: int) -> None:
        d = run_date.isoformat()
        c = self.conn
        c.execute("DELETE FROM candidates WHERE run_date=?", (d,))
        c.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
                  (d, regime.get("score"), regime.get("label"), len(results), n_picks))
        rows = []
        for r in results:
            sig = {k: [round(s.score, 4), round(s.conf, 3)] for k, s in r["signals"].items()}
            rows.append((d, r["ticker"], r["price"], r["upside"], r["risk"], r["coverage"], int(r["pick"]),
                         json.dumps(sig), json.dumps(r["why"], ensure_ascii=False)))
        c.executemany("INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?,?,?,?)", rows)
        c.commit()

    # ------------------------------------------------------------ evaluation
    def pending(self, today: date) -> list[tuple[str, str, float, int]]:
        out = []
        for h in HORIZONS:
            cutoff = (today - timedelta(days=h)).isoformat()
            q = """SELECT c.run_date, c.ticker, c.price FROM candidates c
                   LEFT JOIN evaluations e ON e.run_date=c.run_date AND e.ticker=c.ticker AND e.horizon=?
                   WHERE e.ticker IS NULL AND c.run_date<=?"""
            out += [(rd, t, p, h) for rd, t, p in self.conn.execute(q, (h, cutoff))]
        return out

    def evaluate(self, panel: dict[str, pd.DataFrame], today: date) -> int:
        close, high = panel["Close"], panel["High"]
        if close.empty or "SPY" not in close.columns:
            return 0
        spy = close["SPY"].dropna()
        rows = []
        for run_date, ticker, price, h in self.pending(today):
            if ticker not in close.columns or not price:
                continue
            s = close[ticker].dropna()
            start, target = pd.Timestamp(run_date), pd.Timestamp(run_date) + pd.Timedelta(days=h)
            after = s[s.index >= target]
            if after.empty:
                continue
            end_date = after.index[0]
            ret = float(after.iloc[0] / price - 1)
            spy_start = spy[spy.index <= start]
            spy_end = spy[spy.index >= target]
            spy_ret = float(spy_end.iloc[0] / spy_start.iloc[-1] - 1) if len(spy_start) and len(spy_end) else 0.0
            window = high[ticker][(high.index > start) & (high.index <= end_date)].dropna()
            max_gain = float(window.max() / price - 1) if len(window) else ret
            rows.append((run_date, ticker, h, ret, spy_ret, ret - spy_ret, max_gain))
        self.conn.executemany("INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def evaluated_frame(self, horizon: int) -> pd.DataFrame:
        q = """SELECT c.run_date, c.ticker, c.upside, c.risk, c.pick, c.signals, e.ret, e.excess, e.max_gain
               FROM candidates c JOIN evaluations e ON e.run_date=c.run_date AND e.ticker=c.ticker
               WHERE e.horizon=?"""
        return pd.read_sql_query(q, self.conn, params=(horizon,))

    # ------------------------------------------------------------ learning
    def learn(self, signal_names: list[str], cfg: dict, skip: set | None = None) -> dict:
        """30-day check of every signal. `skip`: signals whose weight the self-review already sets from the
        plan-based results (it has the better measure once there is enough data)."""
        skip = skip or set()
        h = int(cfg.get("horizon_days", 30))
        h = h if h in HORIZONS else 30
        df = self.evaluated_frame(h)
        stats = {"n": len(df), "signals": {}, "factors": {}}
        if df.empty:
            return stats
        parsed = df["signals"].apply(json.loads)
        for name in signal_names:
            sc = parsed.apply(lambda d, n=name: d.get(n, [None, 0]))
            mask = sc.apply(lambda x: x[0] is not None and x[1] > 0)
            if mask.sum() < 10:
                continue
            x = sc[mask].apply(lambda v: v[0]).astype(float)
            y = df.loc[mask, "excess"].astype(float)
            ic = spearman(x, y)
            ic = 0.0 if np.isnan(ic) else ic
            n = int(mask.sum())
            stats["signals"][name] = {"ic": ic, "n": n}
            if n >= cfg.get("min_samples", 60) and name not in skip:
                f = clip(1 + cfg.get("gain", 5.0) * ic, cfg.get("min_factor", 0.25), cfg.get("max_factor", 2.0))
                stats["factors"][name] = f
                self.conn.execute("INSERT OR REPLACE INTO weights VALUES (?,?,?,?,?)",
                                  (name, f, ic, n, date.today().isoformat()))
        self.conn.commit()
        picks = df[df["pick"] == 1]
        stats["picks"] = {
            "n": len(picks),
            "avg_excess": float(picks["excess"].mean()) if len(picks) else None,
            "hit_rate": float((picks["excess"] > 0).mean()) if len(picks) else None,
            "explosive_rate": float((picks["max_gain"] >= 0.30).mean()) if len(picks) else None,
        }
        stats["all"] = {
            "avg_excess": float(df["excess"].mean()),
            "explosive_rate": float((df["max_gain"] >= 0.30).mean()),
        }
        stats["horizon"] = h
        return stats

    def factors(self) -> dict[str, float]:
        return {s: f for s, f in self.conn.execute("SELECT signal, factor FROM weights")}

    # ------------------------------------------------------------ analyst firms
    def add_firm_calls(self, calls: list[dict]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO firm_calls VALUES (?,?,?,?,?,?)",
            [(c["firm"], c["ticker"], c["date"], c["direction"], c["excess"], c["hit"]) for c in calls])
        self.conn.commit()

    def firm_calls(self) -> list[dict]:
        cur = self.conn.execute("SELECT firm, ticker, date, direction, excess, hit FROM firm_calls")
        return [dict(zip(("firm", "ticker", "date", "direction", "excess", "hit"), r)) for r in cur]

    # ------------------------------------------------------------ options history
    def add_option_volume(self, ticker: str, d: date, total: float) -> None:
        self.conn.execute("INSERT OR REPLACE INTO options_hist VALUES (?,?,?)", (ticker, d.isoformat(), total))

    def option_history(self, ticker: str, before: date, n: int = 30) -> list[float]:
        cur = self.conn.execute("SELECT total_vol FROM options_hist WHERE ticker=? AND date<? ORDER BY date DESC LIMIT ?",
                                (ticker, before.isoformat(), n))
        return [r[0] for r in cur]

    # ------------------------------------------------------------ paper trading
    # A pick becomes a 'pending' order. It is filled at the open of the first trading day after the prices the scan
    # used (what you could really get), keeping the same dollar distance to the stop.
    def _rows(self, where: str = "") -> list[dict]:
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(paper)")]
        return [dict(zip(cols, r)) for r in self.conn.execute(f"SELECT * FROM paper {where} ORDER BY entry_date, id")]

    def open_positions(self) -> list[dict]:
        return self._rows("WHERE status IN ('open', 'pending')")

    def paper_open(self, picks: list[dict], today: date, max_open: int, bar_date: date | None = None,
                   max_new: int = 0, plan: dict | None = None) -> int:
        """bar_date: the day of the last prices the scan used (the order fills on the next trading day's open).
        max_new: at most this many new positions a day (0 = no limit), so the portfolio keeps taking new picks
        over time instead of filling up on the first day."""
        held = {p["ticker"] for p in self.open_positions()}
        n_open, opened = len(held), 0
        ref = (bar_date or today).isoformat()
        for r in picks:
            pl = r.get("plan") or {}
            if r["ticker"] in held or n_open >= max_open or not pl.get("shares"):
                continue
            if max_new and opened >= max_new:
                break
            self.conn.execute(
                "INSERT INTO paper (ticker, entry_date, entry_price, initial_stop, stop, atr, shares, status, last_price, plan) "
                "VALUES (?,?,?,?,?,?,?, 'pending', ?, ?)",
                (r["ticker"], ref, r["price"], pl["stop"], pl["stop"], r.get("atr"), pl["shares"], r["price"],
                 plan_text(plan)))
            n_open += 1
            opened += 1
        self.conn.commit()
        return opened

    def paper_update(self, panel: dict[str, pd.DataFrame], today: date, cfg: dict, atr_mult: float,
                     target_r: float | None = None, default_plan: dict | None = None) -> None:
        """Replays every open position from its entry with its plan: stop, target (entry + target_r x the initial
        risk) or none, trailing stop or fixed, and a maximum holding time in trading days."""
        o, h, lo, c = panel["Open"], panel["High"], panel["Low"], panel["Close"]
        for p in self.open_positions():
            t = p["ticker"]
            if t not in c.columns:
                continue
            pl = plan_from_text(p.get("plan"), default_plan)
            tr_ = pl.get("target_r") if pl else target_r
            if pl is not None and not cfg.get("take_profit", True):
                tr_ = None
            trail = (pl or {}).get("trail_atr") or (atr_mult if cfg.get("trailing_stop") else None)
            window = int((pl or {}).get("window") or 0)
            bars = pd.DataFrame({"o": o[t], "h": h[t], "l": lo[t], "c": c[t]}).dropna()
            bars = bars[bars.index > pd.Timestamp(p["entry_date"])]
            if p["status"] == "pending":
                if bars.empty:
                    continue
                fill_day = bars.index[0]
                dist = max(0.01, p["entry_price"] - p["initial_stop"])
                fill = float(bars["o"].iloc[0])
                p.update(entry_date=fill_day.date().isoformat(), entry_price=fill,
                         initial_stop=max(0.01, fill - dist), stop=max(0.01, fill - dist), status="open")
                self.conn.execute("UPDATE paper SET entry_date=?, entry_price=?, initial_stop=?, stop=?, status='open' "
                                  "WHERE id=?", (p["entry_date"], fill, p["initial_stop"], p["stop"], p["id"]))
                bars = bars[bars.index >= fill_day]
                first_is_fill = True
            else:
                bars = pd.DataFrame({"o": o[t], "h": h[t], "l": lo[t], "c": c[t]}).dropna()
                bars = bars[bars.index >= pd.Timestamp(p["entry_date"])]   # replay from the fill day itself
                first_is_fill = True
            stop, high_close = p["initial_stop"], p["entry_price"]   # replay from the entry: same result every day
            risk = p["entry_price"] - p["initial_stop"]
            target = p["entry_price"] + tr_ * risk if tr_ and risk > 0 else None
            exit_price = exit_date = reason = None
            for i, (d, b) in enumerate(bars.iterrows()):
                first = first_is_fill and i == 0
                if not first and b["o"] <= stop:   # gapped through the stop
                    exit_price, exit_date, reason = b["o"], d, "stop"
                    break
                if target and not first and b["o"] >= target:   # gapped above the target
                    exit_price, exit_date, reason = b["o"], d, "target"
                    break
                if b["l"] <= stop:   # a day that touches both counts as the stop (conservative)
                    exit_price, exit_date, reason = stop, d, "stop"
                    break
                if target and b["h"] >= target:
                    exit_price, exit_date, reason = target, d, "target"
                    break
                high_close = max(high_close, b["c"])
                if trail and p["atr"]:
                    stop = max(stop, high_close - trail * p["atr"])
                if window and i + 1 >= window:   # held the plan's number of trading days: sell at the close
                    exit_price, exit_date, reason = b["c"], d, "time"
                    break
            last = float(bars["c"].iloc[-1]) if len(bars) else (p.get("last_price") or p["entry_price"])
            held_days = (today - date.fromisoformat(p["entry_date"])).days
            if reason is None and not window and held_days >= cfg.get("max_hold_days", 90) and len(bars):
                exit_price, exit_date, reason = last, bars.index[-1], "time"
            if reason:
                self.conn.execute("UPDATE paper SET status='closed', exit_date=?, exit_price=?, exit_reason=?, stop=?, "
                                  "last_price=? WHERE id=?",
                                  (pd.Timestamp(exit_date).date().isoformat(), float(exit_price), reason, stop,
                                   float(exit_price), p["id"]))
            else:
                self.conn.execute("UPDATE paper SET stop=?, last_price=? WHERE id=?", (stop, last, p["id"]))
        self.conn.commit()

    def paper_summary(self, spy: pd.Series | None, target_r: float | None = None,
                      default_plan: dict | None = None) -> dict:
        rows = self._rows()
        if not rows:
            return {"n": 0}
        closed = [r for r in rows if r["status"] == "closed"]
        open_ = [r for r in rows if r["status"] == "open"]
        pending = [r for r in rows if r["status"] == "pending"]
        pnl = lambda r, px: (px - r["entry_price"]) * r["shares"]  # noqa: E731
        realized = sum(pnl(r, r["exit_price"]) for r in closed)
        unrealized = sum(pnl(r, r["last_price"] or r["entry_price"]) for r in open_)
        invested = sum(r["entry_price"] * r["shares"] for r in closed + open_)
        rets = [r["exit_price"] / r["entry_price"] - 1 for r in closed]
        wins = [x for x in rets if x > 0]
        losses = [x for x in rets if x <= 0]
        first = rows[0]["entry_date"]
        spy_ret = None
        if spy is not None and len(spy.dropna()):
            s = spy.dropna()
            base = s[s.index <= pd.Timestamp(first)]
            if len(base):
                spy_ret = float(s.iloc[-1] / base.iloc[-1] - 1)
        def tgt(r):
            pl = plan_from_text(r.get("plan"), default_plan)
            tr_ = (pl or {}).get("target_r") if pl else target_r
            if not target_r and pl is None:
                return None
            return (r["entry_price"] + tr_ * (r["entry_price"] - r["initial_stop"])
                    if tr_ and r["initial_stop"] and r["entry_price"] > r["initial_stop"] else None)
        pos = lambda r, st: {"ticker": r["ticker"], "entry": r["entry_price"], "last": r["last_price"],  # noqa: E731
                             "stop": r["stop"], "target": tgt(r), "since": r["entry_date"], "status": st,
                             "shares": r["shares"],
                             "ret": (r["last_price"] or r["entry_price"]) / r["entry_price"] - 1 if st == "open" else 0.0}
        return {
            "n": len(rows), "open": len(open_), "pending": len(pending), "closed": len(closed), "since": first,
            "realized": realized, "unrealized": unrealized,
            "return_on_invested": (realized + unrealized) / invested if invested else None,
            "win_rate": len(wins) / len(rets) if rets else None,
            "avg_win": float(np.mean(wins)) if wins else None,
            "avg_loss": float(np.mean(losses)) if losses else None,
            "spy_ret": spy_ret,
            "open_positions": [pos(r, "open") for r in open_] + [pos(r, "pending") for r in pending],
            "closed_positions": [{"ticker": r["ticker"], "entry": r["entry_price"], "exit": r["exit_price"],
                                  "since": r["entry_date"], "until": r["exit_date"], "reason": r["exit_reason"],
                                  "ret": r["exit_price"] / r["entry_price"] - 1} for r in closed[-30:]][::-1],
        }
