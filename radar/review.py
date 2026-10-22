"""Self-review: the system grades its own past recommendations, finds where it was right and wrong, and
changes itself only where the evidence holds up.

1. record()  every scan stores what it predicted for each stock it analysed in depth: the plan (stop, target),
   the chance it showed, what its probability level earned in the model's test, the model features, the signals,
   the reasons it gave, and why a stock was or was not picked.
2. update_outcomes()  replays the plan on what really happened, with the same rules as the training and the
   paper portfolio (next open, stop, 3R target, 63 trading days, gaps, costs). Every recommendation is also
   graded after 21 trading days, so the review is not biased towards trades that ended fast.
3. run()  grades itself and writes the conclusions in Hebrew:
     scorecard     picks vs. the stocks it analysed the same day and did not pick vs. the S&P 500
     calibration   the chance it showed vs. how many really reached the target
     post-mortems  for each closed pick: what it said, what happened, and why (market, report, reversal, entry)
     signals       which signals helped and which misled -> their weights in the score change
     conditions    in which situations its recommendations lose -> suspected lessons
     filters       did a filter skip stocks that then did better than the picks?
     drift         is the model doing worse live than in its test? -> less weight + early retraining
4. A suspected lesson changes nothing until 5 years of history confirm it (train.py runs test_lessons for every
   condition). Confirmed lessons filter the picks; rejected ones are reported as bad luck, not a pattern. A lesson
   whose skipped stocks then do better than the picks is revoked."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .journal import plan_from_text, plan_text, spearman
from .signals import SIGNAL_LABELS
from .util import clip, num, pct

EARLY = 21            # trading days: the first, unbiased grade of every recommendation
IDEA_GAP = 21         # calendar days: the same stock recommended again within 3 weeks is the same idea
PRIOR_N = 100         # how many live trades the test result of a probability level is worth (shrinkage)
MIN_IDEAS = 20        # ideas needed before the review judges anything

# thresholds of the conditions: (quantile over all stocks in the training data, default before a model exists)
QUANT = {"atr_pct": (0.8, 0.055), "ret_21": (0.9, 0.20), "max_ret_21": (0.9, 0.09), "ret_5": (0.1, -0.07),
         "log_dollar_vol": (0.2, 7.3), "log_price": (0.2, 1.15), "dist_high": (0.2, -0.35),
         "ind_rs": (0.2, -0.08), "squeeze": (0.8, 3.0)}

# situations the review checks: (feature, test, level, text, short text). level 'q' = the threshold above.
# All of them exist in the 5-year training data, so a lesson found live can be confirmed on history first.
CONDITIONS = {
    "high_vol": ("atr_pct", ">=", "q", "מניות תנודתיות מאוד (20% התנודתיות בשוק)", "תנודתית מאוד"),
    "hot_month": ("ret_21", ">=", "q", "מניות שכבר עלו חזק בחודש שלפני (10% העליונות)", "עלתה חזק בחודש שלפני"),
    "overbought": ("rsi14", ">=", 0.75, "מניות עם RSI מעל 75 (קנויות יתר על המידה)", "RSI מעל 75"),
    "spike": ("max_ret_21", ">=", "q", "מניות עם קפיצה חדה של יום אחד בחודש האחרון", "קפיצה חדה של יום אחד"),
    "falling_week": ("ret_5", "<=", "q", "מניות שירדו חזק בשבוע שלפני (10% התחתונות)", "ירדה חזק בשבוע שלפני"),
    "low_liq": ("log_dollar_vol", "<=", "q", "מניות עם נזילות נמוכה (20% הפחות נזילות)", "נזילות נמוכה"),
    "cheap": ("log_price", "<=", "q", "מניות במחיר נמוך (20% הזולות)", "מחיר מניה נמוך"),
    "below_50": ("above_50", "==", 0.0, "מניות מתחת לממוצע 50 יום", "מתחת לממוצע 50 יום"),
    "far_high": ("dist_high", "<=", "q", "מניות רחוקות מאוד מהשיא השנתי (20% הרחוקות)", "רחוקה מהשיא השנתי"),
    "weak_industry": ("ind_rs", "<=", "q", "מניות בענף חלש מהשוק (20% הענפים החלשים)", "ענף חלש"),
    "mkt_down": ("mkt_above_200", "==", 0.0, "כשהשוק כולו במגמה יורדת (S&P 500 מתחת לממוצע 200)", "השוק במגמה יורדת"),
    "earn_miss": ("earn_surprise", "<", 0.0, "מניות שפספסו את תחזית הרווח בדוח האחרון", "פספסה תחזית בדוח"),
    "wide": ("squeeze", ">=", "q", "מניות עם תנודה רחבה מהרגיל (20% העליונות)", "תנודה רחבה מהרגיל"),
}
# situations known only live (no history to confirm them): a lesson needs much stronger live evidence
LIVE_ONLY = {
    "earn_soon": ("מניות עם דוח רבעוני צפוי בתוך 10 ימים", "דוח רבעוני קרוב"),
    "risk_off": ("כשהמאקרו הצביע על שוק מסוכן", "מאקרו מסוכן"),
    "extended": ("מניות 25% ומעלה מעל ממוצע 50 יום", "מתוחה מעל ממוצע 50"),
    "tg": ("מניות שהוזכרו בערוץ הטלגרם בשבוע שלפני", "הוזכרה בטלגרם"),
}
WHY_NOT_HE = {
    "upside": "ציון הפוטנציאל היה נמוך מהסף", "risk": "ציון הסיכון היה גבוה מהסף",
    "edge": "ברמת הסיכוי שלהן עסקאות הפסידו בבדיקה", "deal": "בתהליך רכישה או פשיטת רגל",
    "lesson": "לקח שלמדתי", "rank": "לא נכנסו לרשימת המובילות",
    "band": "לא היו בין המובילות של המודל", "sector": "כבר נבחרו 3 מניות מאותו ענף",
    "filters": "לא עברו את מסנני הנזילות והמחיר (נבדקו כי נשמרו)",
}


# ------------------------------------------------------------------ conditions
def default_thresholds() -> dict:
    return {k: v[1] for k, v in QUANT.items()}


def compute_thresholds(D: pd.DataFrame) -> dict:
    """Thresholds of the conditions over all training rows (saved in model.json, used live too)."""
    out = default_thresholds()
    for k, (q, _d) in QUANT.items():
        if k in D:
            x = pd.to_numeric(D[k], errors="coerce").to_numpy(dtype=float)
            x = x[np.isfinite(x)]
            if len(x) >= 100:
                out[k] = float(np.quantile(x, q))
    return out


def cond_frame(df: pd.DataFrame, thr: dict | None = None, live: bool = True) -> dict[str, pd.Series]:
    """For each condition, per row: 1.0 = in that situation, 0.0 = not, NaN = unknown."""
    thr = {**default_thresholds(), **(thr or {})}
    col = lambda k: pd.to_numeric(df[k], errors="coerce") if k in df else pd.Series(np.nan, index=df.index)  # noqa: E731
    out = {}
    for key, (feat, op, lvl, _t, _s) in CONDITIONS.items():
        if key == "earn_miss":
            rec, sur = col("earn_recent"), col("earn_surprise")
            known = rec.notna() & ((rec != 1) | sur.notna())
            val = (rec == 1) & (sur < 0)
        else:
            x = col(feat)
            v = thr.get(feat, QUANT.get(feat, (0, 0))[1]) if lvl == "q" else lvl
            known = x.notna()
            val = x >= v if op == ">=" else x <= v if op == "<=" else x == v
        out[key] = val.astype(float).where(known)
    if live:
        if "earnings_date" in df and "run_date" in df:
            e = pd.to_datetime(df["earnings_date"].astype("string").str[:10], errors="coerce")
            r = pd.to_datetime(df["run_date"].astype("string").str[:10], errors="coerce")
            days = (e - r).dt.days
            out["earn_soon"] = days.between(0, 10).astype(float).where(days.notna())
        if "risk_off" in df:
            ro = col("risk_off")
            out["risk_off"] = (ro == 1).astype(float).where(ro.notna())
        if "ext50" in df:
            x = col("ext50")
            out["extended"] = (x >= 0.25).astype(float).where(x.notna())
        if "tg" in df:
            x = col("tg")
            out["tg"] = (x > 0).astype(float).where(x.notna())
    return out


def cond_text(key: str, short: bool = False) -> str:
    if key in CONDITIONS:
        return CONDITIONS[key][4 if short else 3]
    if key in LIVE_ONLY:
        return LIVE_ONLY[key][1 if short else 0]
    return key


def test_lessons(T: pd.DataFrame, sel_pos, fin_pos, score_col: str, top_n: int, thr: dict) -> dict:
    """Training: for every condition, would skipping it have improved the model's top picks?
    Measured on period A (selection) and period B (final check). Used to confirm or reject lessons found live."""
    conds = cond_frame(T, thr, live=False)
    out = {}
    for key, c in conds.items():
        bad = c.fillna(0).to_numpy(dtype=bool)
        res = {}
        for name, ps in (("sel", sel_pos), ("fin", fin_pos)):
            part = T["pos"].isin(ps).to_numpy()
            P = T[part]
            top = P.sort_values(score_col, ascending=False).groupby("pos", sort=False).head(top_n)
            keep = P[~bad[part]]
            topf = keep.sort_values(score_col, ascending=False).groupby("pos", sort=False).head(top_n)
            if not len(top) or not len(topf):
                res[name] = None
                continue
            base, filt = float(top["ret"].mean()), float(topf["ret"].mean())
            share = float(bad[part][np.isin(P.index, top.index)].mean()) if len(top) else 0.0
            res[name] = {"base": base, "filtered": filt, "delta": filt - base, "share": share}
        s, f = res.get("sel"), res.get("fin")
        passed = bool(s and f and s["share"] >= 0.03 and s["delta"] > 0.001 and f["delta"] >= -0.001)
        out[key] = {"sel": s, "fin": f, "passed": passed}
    return out


# ------------------------------------------------------------------ recording
def model_id(model) -> str | None:
    if model is None:
        return None
    return f"{model.kind}:{model.meta.get('trained')}"


# The journal is saved in the repository after every run, so rows are stored compactly: the names of the features
# and signals are kept once (table kv), each row keeps only the values, rounded to 4 significant digits.
def _keyset(conn, name: str, keys: list[str], cache: dict) -> int:
    t = tuple(keys)
    if (name, t) in cache:
        return cache[(name, t)]
    rows = conn.execute("SELECT key, value FROM kv WHERE key LIKE ?", (f"keys:{name}:%",)).fetchall()
    for k, v in rows:
        if tuple(json.loads(v)) == t:
            cache[(name, t)] = int(k.rsplit(":", 1)[1])
            return cache[(name, t)]
    i = len(rows)
    conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (f"keys:{name}:{i}", json.dumps(list(t))))
    cache[(name, t)] = i
    return i


def _pack(conn, name: str, d: dict, cache: dict) -> str | None:
    if not d:
        return None
    keys = sorted(d)
    vals = []
    for k in keys:
        v = num(d[k])
        vals.append(None if v is None else float(f"{v:.4g}"))
    return json.dumps({"k": _keyset(conn, name, keys, cache), "v": vals}, separators=(",", ":"))


def _unpacker(conn):
    sets = {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM kv WHERE key LIKE 'keys:%'")}

    def unpack(name: str, s) -> dict:
        if not isinstance(s, str) or not s:
            return {}
        try:
            d = json.loads(s)
        except ValueError:
            return {}
        if isinstance(d, dict) and "k" in d and "v" in d:
            keys = sets.get(f"keys:{name}:{d['k']}") or []
            return dict(zip(keys, d["v"]))
        if name == "sig" and isinstance(d, dict):     # the candidates table's format: {signal: [score, conf]}
            return {k: (v[0] if isinstance(v, list) and len(v) > 1 and v[1] > 0 else None) for k, v in d.items()}
        return d if isinstance(d, dict) else {}
    return unpack


def _sig_scores(signals: dict) -> dict:
    """Signal score where the signal had data (confidence > 0), None where it did not."""
    return {k: (round(float(s.score), 3) if s.conf > 0 else None) for k, s in (signals or {}).items()}


def record(journal, today: date, bar_date: date, results: list[dict], ctx, model, cost_base: float,
           plan: dict | None = None) -> int:
    """Store what the scan predicted for every stock it analysed in depth."""
    from .train import trade_costs

    mid = model_id(model) if model is not None and model.quality > 0 else None
    risk_off = int(bool((ctx.regime or {}).get("risk_off"))) if ctx is not None else None
    rows, cache, conn = [], {}, journal.conn
    for r in results:
        feats = r.get("_feats") or {}
        dv = num(r.get("dollar_vol"))
        cost = float(trade_costs(np.array([dv if dv else np.nan]), cost_base)[0])
        hist = r.get("prob_hist") or {}
        ed = r.get("earnings_date")
        rows.append((
            today.isoformat(), r["ticker"], bar_date.isoformat(), int(bool(r.get("pick"))), r.get("why_not"),
            num(r.get("price")), num(r.get("atr")), cost, num(r.get("prob")), num(hist.get("ret")),
            r.get("bucket"), mid if r.get("prob") is not None else None, num(r.get("upside")), num(r.get("risk")),
            r.get("sector") or "", str(ed)[:10] if ed else None, risk_off, num(r.get("ext50")),
            _pack(conn, "feat", feats, cache), _pack(conn, "sig", _sig_scores(r.get("signals")), cache),
            json.dumps((r.get("why") or {}).get("pros", [])[:3], ensure_ascii=False) if r.get("pick") else None,
            plan_text(plan), r.get("tg_n")))
    journal.conn.execute("DELETE FROM predictions WHERE run_date=?", (today.isoformat(),))
    journal.conn.executemany("INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             rows)
    journal.conn.commit()
    return len(rows)


def _backfill(conn, panel, fpanel, cost_base: float) -> int:
    """Recommendations logged before the self-review existed: rebuild the plan from the price history."""
    from .features import market_at, tech_features
    from .train import trade_costs

    q = """SELECT c.run_date, c.ticker, c.price, c.upside, c.risk, c.pick, c.signals, c.reasons FROM candidates c
           LEFT JOIN predictions p ON p.run_date=c.run_date AND p.ticker=c.ticker WHERE p.ticker IS NULL"""
    todo = conn.execute(q).fetchall()
    if not todo:
        return 0
    idx = panel["Close"].index
    spy = panel["Close"]["SPY"] if "SPY" in panel["Close"].columns else None
    keys = ("close", "sma50", "sma200", "vol_ratio", "dollar_vol20", "rsi14", "mom_6_1", "rs_63", "ret_21", "ret_5",
            "max_ret_21", "dist_high", "squeeze", "atr_pct")
    rows, cache = [], {}
    unpack = _unpacker(conn)
    for run_date, t, price, up, risk, pick, sig, reasons in todo:
        p = int(idx.searchsorted(pd.Timestamp(run_date), "left")) - 1   # the last close before the scan day
        atr = cost = feats = None
        bar = None
        if p >= 0 and t in fpanel["atr14"].columns:
            bar = idx[p].date().isoformat()
            atr = num(fpanel["atr14"][t].iloc[p])
            dv = num(fpanel["dollar_vol20"][t].iloc[p])
            cost = float(trade_costs(np.array([dv if dv else np.nan]), cost_base)[0])
            f = tech_features({k: fpanel[k][t].iloc[p] for k in keys if k in fpanel})
            f.update(market_at(spy, p) if spy is not None else {})
            feats = _pack(conn, "feat", f, cache)
        try:
            pros = (json.loads(reasons) or {}).get("pros", [])[:3]
        except (TypeError, ValueError):
            pros = []
        rows.append((run_date, t, bar, int(pick or 0), None if pick else "unknown", price, atr, cost, None, None, None,
                     None, up, risk, "", None, None, None, feats, _pack(conn, "sig", unpack("sig", sig), cache),
                     json.dumps(pros, ensure_ascii=False) if pick else None, None, None))
    conn.executemany("INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def update_outcomes(journal, panel, fpanel, settings) -> int:
    """Replay the plan of every recommendation that is not finished yet (same code as the training). Each
    recommendation keeps the exit plan that was in force when it was made."""
    from .train import base_plan, simulate_trades

    conn = journal.conn
    default = base_plan(settings)
    cost_base = float(settings.get("train.cost_pct") or 0.2) / 100
    _backfill(conn, panel, fpanel, cost_base)
    rows = conn.execute("""SELECT p.run_date, p.ticker, p.bar_date, p.atr, p.cost, p.plan FROM predictions p
        LEFT JOIN outcomes o ON o.run_date=p.run_date AND o.ticker=p.ticker
        WHERE p.atr > 0 AND p.bar_date IS NOT NULL AND (o.ticker IS NULL OR o.status='open' OR o.ret21 IS NULL)""").fetchall()
    if not rows:
        return 0
    O, H, L, C = (panel[x] for x in ("Open", "High", "Low", "Close"))
    idx = C.index
    spy_o = O["SPY"].ffill().to_numpy(dtype=float) if "SPY" in O.columns else None
    spy_c = C["SPY"].ffill().to_numpy(dtype=float) if "SPY" in C.columns else None
    groups = defaultdict(list)
    for r in rows:
        if r[1] in C.columns:
            groups[(r[2], r[5] or "")].append(r)
    out = []
    for (bar_date, ptxt), g in groups.items():
        pl = plan_from_text(ptxt, default) or default
        k, target_r, window = float(pl["stop_atr"]), pl.get("target_r"), int(pl.get("window") or 63)
        trail = pl.get("trail_atr")
        p = int(idx.searchsorted(pd.Timestamp(bar_date), "right")) - 1
        avail = len(idx) - p - 1
        if p < 0 or avail < 1:
            continue
        cols = [r[1] for r in g]
        mats = [x[cols].to_numpy(dtype=float) for x in (O, H, L, C)]
        atr = np.full(mats[0].shape, np.nan)
        atr[p] = [r[3] for r in g]
        cost = np.array([[r[4] if r[4] is not None else cost_base for r in g]])
        w = min(window, avail)
        res = simulate_trades(*mats, atr, [p], w, k, target_r, None, cost, spy_o, spy_c, trail)
        r21 = (simulate_trades(*mats, atr, [p], min(EARLY, window), k, target_r, None, cost, spy_o, spy_c, trail)
               if avail >= min(EARLY, window) else None)
        for j, r in enumerate(g):
            ret = res["ret"][0, j]
            if not np.isfinite(ret):
                continue
            kind, held = int(res["kind"][0, j]), int(res["days"][0, j])
            e = mats[0][p + 1, j]
            risk = k * r[3]
            closed = kind != 0 or w == window
            hi = np.nanmax(mats[1][p + 1:p + 1 + held, j]) if held > 0 else e
            c = float(cost[0, j])
            out.append((r[0], r[1], "closed" if closed else "open", idx[p + 1].date().isoformat(), float(e),
                        float(e - risk), float(e + target_r * risk) if target_r else None,
                        idx[min(p + held, len(idx) - 1)].date().isoformat() if closed else None,
                        float(e * (1 + ret + c)), kind if closed else 0, float(ret), float(res["r"][0, j]), held,
                        num(res["spy"][0, j]), float((hi - e) / risk) if risk > 0 and np.isfinite(hi) else None,
                        num(r21["ret"][0, j]) if r21 is not None else None,
                        num(r21["spy"][0, j]) if r21 is not None else None))
    conn.executemany("INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    conn.commit()
    return len(out)


# ------------------------------------------------------------------ analysis helpers
def _frame(conn) -> pd.DataFrame:
    q = """SELECT p.*, o.status, o.entry_date, o.entry, o.stop, o.target, o.exit_date, o.exit, o.kind, o.ret, o.r,
                  o.days, o.spy, o.mfe_r, o.ret21, o.spy21
           FROM predictions p LEFT JOIN outcomes o ON o.run_date=p.run_date AND o.ticker=p.ticker"""
    df = pd.read_sql_query(q, conn)
    if df.empty:
        return df
    unpack = _unpacker(conn)
    df["sig"] = [unpack("sig", x) for x in df["signals"]]
    F = pd.DataFrame([unpack("feat", x) for x in df["feats"]], index=df.index)
    F = F[[c for c in F.columns if c not in df.columns]]
    df = pd.concat([df, F.apply(pd.to_numeric, errors="coerce")], axis=1)
    for c in ("ret", "ret21", "spy", "spy21", "mfe_r", "prob", "exp_ret", "kind", "days"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    m = df["ret21"].notna()
    df["rx21"] = np.nan
    df.loc[m, "rx21"] = df.loc[m, "ret21"] - df[m].groupby("run_date")["ret21"].transform("mean")
    return df


def ideas(df: pd.DataFrame, gap: int = IDEA_GAP) -> pd.DataFrame:
    """One row per idea, like a real portfolio: a stock recommended again while its trade is still open, or within
    `gap` days, is the same idea (its first day counts). Whether the earlier trade had ended is known on the day of
    the new recommendation, so this adds no hindsight."""
    if df.empty:
        return df
    d = df.sort_values(["run_date", "ticker"])
    none = pd.Series(None, index=d.index, dtype=object)
    exits = d["exit_date"] if "exit_date" in d else none
    status = d["status"] if "status" in d else none
    keep, last = [], {}
    for i, t, rd, ex, st in zip(d.index, d["ticker"], d["run_date"], exits, status):
        rd = str(rd)[:10]
        day = date.fromisoformat(rd).toordinal()
        prev = last.get(t)
        if prev is not None:
            pday, pexit, pstatus = prev
            still_open = pstatus == "open" or (pexit is not None and pexit >= rd)
            if day - pday < gap or still_open:
                continue
        keep.append(i)
        last[t] = (day, str(ex)[:10] if isinstance(ex, str) and ex else None, st if isinstance(st, str) else None)
    return d.loc[keep]


def _mt(x) -> dict:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if not n:
        return {"n": 0, "mean": None, "t": None}
    m = float(x.mean())
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    return {"n": n, "mean": m, "t": m / (sd / math.sqrt(n)) if n > 2 and sd > 0 else None}


def _welch(a, b) -> dict:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    out = {"n_a": len(a), "n_b": len(b), "a": float(a.mean()) if len(a) else None,
           "b": float(b.mean()) if len(b) else None, "diff": None, "t": None}
    if len(a) >= 3 and len(b) >= 3:
        out["diff"] = out["a"] - out["b"]
        se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        out["t"] = out["diff"] / se if se > 0 else None
    return out


def _f(x, d=1):
    return pct(x, d, True) if x is not None and np.isfinite(x) else "–"


# ------------------------------------------------------------------ the review
def post_mortem(row: pd.Series, conds: dict[str, float]) -> dict:
    """What I said, what happened, and why."""
    kind, ret, days = int(row["kind"] or 0), float(row["ret"]), int(row["days"] or 0)
    spy = row["spy"] if pd.notna(row["spy"]) else None
    mfe = row["mfe_r"] if pd.notna(row["mfe_r"]) else None
    tags, causes = [], []
    if kind == 1:
        verdict = "right"
        head = f"צדקתי: הגיעה ליעד אחרי {days} ימי מסחר ({_f(ret)})"
        if spy is not None and spy >= 0.05:
            causes.append(f"חלק מהרווח בא מהשוק: S&P 500 עלה {_f(spy)} באותם ימים")
    elif ret > 0:
        verdict = "partial"
        head = f"חצי הצלחה: לא הגיעה ליעד תוך 3 חודשים, אבל הרוויחה {_f(ret)}"
    else:
        verdict = "wrong"
        head = (f"טעיתי: נעצרה בסטופ אחרי {days} ימי מסחר ({_f(ret)})" if kind == -1
                else f"טעיתי: נסגרה אחרי 3 חודשים בהפסד ({_f(ret)})")
        if spy is not None and spy <= -0.03:
            tags.append("market")
            causes.append(f"השוק כולו ירד {_f(spy)} באותם ימים – הטעות הייתה בעיקר בתזמון השוק")
        ed, en, ex = row.get("earnings_date"), row.get("entry_date"), row.get("exit_date")
        if ed and en and ex and str(en)[:10] <= str(ed)[:10] <= str(ex)[:10]:
            tags.append("earnings")
            causes.append("דוח רבעוני יצא באמצע העסקה")
        if mfe is not None and mfe >= 1.5:
            tags.append("reversal")
            causes.append(f"הייתה ברווח של פי {mfe:.1f} מהסיכון ואז התהפכה – הכיוון היה נכון בהתחלה")
        if kind == -1 and days <= 5:
            tags.append("fast")
            causes.append(f"נעצרה כבר אחרי {days} ימים – נקודת הכניסה הייתה גרועה")
        bad = [cond_text(k2, True) for k2, v in conds.items() if v == 1 and k2 not in ("mkt_down", "risk_off")]
        if bad:
            tags.append("entry")
            causes.append("בזמן הבחירה: " + ", ".join(bad[:3]))
        if not tags or tags == ["entry"]:
            if spy is None or spy > -0.03:
                tags.insert(0, "stock")
                causes.insert(0, "המניה ירדה בלי שהשוק ירד – הסיבות לקנייה לא התממשו")
    try:
        pros = json.loads(row["pros"]) if isinstance(row.get("pros"), str) else []
    except ValueError:
        pros = []
    return {"ticker": row["ticker"], "date": row["run_date"], "entry_date": row.get("entry_date"),
            "exit_date": row.get("exit_date"), "ret": ret, "kind": kind, "days": days, "spy": spy, "mfe_r": mfe,
            "prob": row["prob"] if pd.notna(row["prob"]) else None, "said": pros[:2], "verdict": verdict,
            "head": head, "causes": causes, "tags": tags}


CAUSE_HE = {"market": "ירידה של השוק כולו", "earnings": "דוח רבעוני באמצע העסקה",
            "reversal": "היו ברווח והתהפכו", "fast": "נעצרו בסטופ תוך שבוע", "entry": "נקודת כניסה בעייתית",
            "stock": "המניה עצמה חלשה בלי שהשוק ירד"}


def signal_audit(C: pd.DataFrame) -> list[dict]:
    """Which signals ranked the stocks it analysed well (21-day result relative to the same day's stocks)."""
    if C.empty:
        return []
    out = []
    for key, label in SIGNAL_LABELS.items():
        sc = C["sig"].map(lambda d, k=key: d.get(k))
        ok = sc.map(lambda v: v is not None and np.isfinite(v)).to_numpy(dtype=bool)
        if ok.sum() < MIN_IDEAS:
            continue
        x = pd.Series([float(v) for v in sc[ok]], dtype=float)
        y = pd.Series(C["rx21"].to_numpy(dtype=float)[ok])
        if x.std() == 0 or y.std() == 0:
            continue
        ic, n = spearman(x, y), int(ok.sum())
        t = ic * math.sqrt(n - 2) / math.sqrt(max(1e-9, 1 - ic * ic))
        strong = (x >= 0.5).to_numpy()
        verdict = "helped" if t >= 2 and ic > 0 else "misled" if t <= -2 and ic < 0 else "unclear"
        out.append({"key": key, "label": label, "ic": ic, "t": t, "n": n, "verdict": verdict,
                    "strong_ret": float(y[strong].mean()) if strong.sum() >= 5 else None,
                    "rest_ret": float(y[~strong].mean()) if (~strong).sum() >= 5 else None})
    return sorted(out, key=lambda s: -s["ic"])


def apply_factors(conn, audit: list[dict], cfg: dict, today: date, logged: dict | None = None) -> tuple[dict, list[str], dict]:
    """Signal weights from the live, plan-based results (replaces the 30-day check once there is enough data).
    Returns the factors, one log line when a weight moved clearly since it was last reported, and the reported values."""
    min_n = int(cfg.get("min_samples", 60))
    gain, lo, hi = float(cfg.get("gain", 5.0)), float(cfg.get("min_factor", 0.25)), float(cfg.get("max_factor", 2.0))
    logged = dict(logged or {})
    factors, ups, downs, n_max = {}, [], [], 0
    for s in audit:
        if s["n"] < min_n or s["key"] == "model":
            continue
        f = clip(1 + gain * s["ic"] * s["n"] / (s["n"] + 100), lo, hi)
        factors[s["key"]] = f
        conn.execute("INSERT OR REPLACE INTO weights VALUES (?,?,?,?,?)", (s["key"], f, s["ic"], s["n"],
                                                                          today.isoformat()))
        prev = logged.get(s["key"], 1.0)
        if abs(f - prev) >= 0.15:
            (ups if f > prev else downs).append(f"{s['label']} מ־×{prev:.2f} ל־×{f:.2f}")
            logged[s["key"]] = f
            n_max = max(n_max, s["n"])
    conn.commit()
    texts = []
    if ups or downs:
        parts = ([f"עלו: {', '.join(ups)}"] if ups else []) + ([f"ירדו: {', '.join(downs)}"] if downs else [])
        texts.append(f"עדכנתי את משקלי האותות לפי {n_max} המלצות שנבדקו. " + "; ".join(parts) + ".")
    return factors, texts, logged


KEEP_CANDIDATES = 130   # days: stocks it analysed but did not pick (their 30/90-day checks are done by then)
KEEP_PREDICTIONS = 400  # days: the same, in the self-review (picks are kept for good)


def prune(journal, today: date) -> int:
    """Keep the journal small enough to be saved in the repository after every run."""
    conn = journal.conn
    c1 = (today - timedelta(days=KEEP_CANDIDATES)).isoformat()
    c2 = (today - timedelta(days=KEEP_PREDICTIONS)).isoformat()
    n = conn.execute("DELETE FROM candidates WHERE pick=0 AND run_date<?", (c1,)).rowcount
    n += conn.execute("""DELETE FROM evaluations WHERE run_date<? AND NOT EXISTS (SELECT 1 FROM candidates c
                         WHERE c.run_date=evaluations.run_date AND c.ticker=evaluations.ticker)""", (c1,)).rowcount
    n += conn.execute("DELETE FROM predictions WHERE pick=0 AND run_date<?", (c2,)).rowcount
    n += conn.execute("""DELETE FROM outcomes WHERE run_date<? AND NOT EXISTS (SELECT 1 FROM predictions p
                         WHERE p.run_date=outcomes.run_date AND p.ticker=outcomes.ticker)""", (c2,)).rowcount
    conn.commit()
    if n > 500:
        conn.execute("VACUUM")
    return n


def load_state(journal) -> dict:
    row = journal.conn.execute("SELECT value FROM kv WHERE key='review_state'").fetchone()
    try:
        return json.loads(row[0]) if row else {}
    except ValueError:
        return {}


def thresholds_for(model) -> dict:
    t = (model.meta.get("thresholds") if model is not None else None) or {}
    return {**default_thresholds(), **t}


def run(journal, panel, fpanel, today: date, settings, model) -> dict:
    """Update the outcomes, grade itself, apply what holds up, and return the review for the app."""
    prune(journal, today)
    update_outcomes(journal, panel, fpanel, settings)
    conn = journal.conn
    prev = load_state(journal)
    thr = thresholds_for(model)
    mid = model_id(model) if model is not None and model.quality > 0 else None
    tests = (model.meta.get("lesson_tests") if model is not None else None) or {}
    df = _frame(conn)
    idx = panel["Close"].index
    review = {"date": today, "model_id": mid, "thresholds": thr}
    if df.empty:
        review["counts"] = {"recorded": 0}
        review["conclusions"] = [{"tone": "info", "text": "עוד לא נרשמו המלצות. מהסריקה הבאה כל המלצה נשמרת עם "
                                                         "מה שחזיתי עליה, ונבדקת אחרי כחודש."}]
        _save_state(conn, prev, {"thresholds": thr, "model_id": mid}, today, [])
        return review
    bar_pos = idx.searchsorted(pd.to_datetime(df["bar_date"].fillna(df["run_date"])), "right") - 1
    df["matured"] = (len(idx) - bar_pos - 1) >= int(settings.get("train.horizon_days") or 63)
    conds = cond_frame(df, thr)
    for k2, v in conds.items():
        df["c_" + k2] = v

    picks_all = ideas(df[df["pick"] == 1])
    others_all = ideas(df[df["pick"] == 0])
    cand_all = ideas(df)
    P, N, Cn = (x[x["ret21"].notna()] for x in (picks_all, others_all, cand_all))
    pending = df[df["ret21"].isna() & df["bar_date"].notna()]
    next_check = None
    if len(pending):
        nb = pd.Timestamp(min(pending["bar_date"])) + pd.Timedelta(days=31)
        next_check = nb.date()
    review["counts"] = {"recorded": int(len(df)), "ideas": int(len(cand_all)), "pick_ideas": int(len(picks_all)),
                        "first": str(df["run_date"].min()), "graded": int(len(Cn)), "graded_picks": int(len(P)),
                        "next_check": next_check}

    # 1. scorecard: after 21 trading days, picks vs. the stocks it analysed and did not pick vs. the S&P 500
    edge = _mt(P["rx21"])
    score = {"n": int(len(P)), "pick_ret": _mt(P["ret21"])["mean"], "pick_win": float((P["ret21"] > 0).mean()) if len(P) else None,
             "pick_spy": _mt(P["spy21"])["mean"], "other_n": int(len(N)), "other_ret": _mt(N["ret21"])["mean"],
             "edge": edge["mean"], "t": edge["t"]}
    score["verdict"] = ("none" if len(P) < MIN_IDEAS else "good" if (edge["t"] or 0) >= 2
                        else "bad" if (edge["t"] or 0) <= -2 else "unclear")
    review["score"] = score

    # 2. final results of the plan (closed picks)
    closed = picks_all[picks_all["status"] == "closed"]
    review["final"] = {"n": int(len(closed)), "open": int((picks_all["status"] == "open").sum()),
                       "target": int((closed["kind"] == 1).sum()), "stop": int((closed["kind"] == -1).sum()),
                       "time": int((closed["kind"] == 0).sum()), "ret": _mt(closed["ret"])["mean"],
                       "win": float((closed["ret"] > 0).mean()) if len(closed) else None,
                       "days": float(closed["days"].mean()) if len(closed) else None,
                       "spy": _mt(closed["spy"])["mean"]}

    # 3. calibration: the chance it showed vs. what happened (only recommendations whose 3 months are over)
    M = cand_all[cand_all["matured"] & cand_all["prob"].notna() & (cand_all["status"] == "closed")]
    cal = {"n": int(len(M))}
    if len(M) >= 30:
        cal.update(prob=float(M["prob"].mean()), actual=float((M["kind"] == 1).mean()),
                   exp_ret=_mt(M["exp_ret"])["mean"], ret=_mt(M["ret"])["mean"])
        q = pd.qcut(M["prob"].rank(method="first"), 3, labels=False)
        cal["groups"] = [{"prob": float(g["prob"].mean()), "actual": float((g["kind"] == 1).mean()),
                          "ret": float(g["ret"].mean()), "n": int(len(g))} for _i, g in M.groupby(q)]
    review["calibration"] = cal
    bucket_live = {}
    if mid:
        for b, g in M[(M["model"] == mid) & M["bucket"].notna()].groupby("bucket"):
            bucket_live[str(int(b))] = [int(len(g)), int((g["kind"] == 1).sum()), float(g["ret"].sum())]

    # 4. post-mortems of closed picks, newest first, and what caused the losses
    pms = []
    for _i, row in closed.sort_values("exit_date", ascending=False).head(30).iterrows():
        pms.append(post_mortem(row, {k2: row["c_" + k2] for k2 in conds}))
    allpm = [post_mortem(row, {k2: row["c_" + k2] for k2 in conds}) for _i, row in closed.iterrows()]
    losses = [p for p in allpm if p["verdict"] == "wrong"]
    causes = defaultdict(int)
    for p in losses:
        causes[p["tags"][0] if p["tags"] else "stock"] += 1
    review["post_mortems"] = pms
    review["loss_causes"] = {"n": len(losses), "by": dict(sorted(causes.items(), key=lambda x: -x[1])),
                             "reversal_any": sum(1 for p in losses if "reversal" in p["tags"]),
                             "market_any": sum(1 for p in losses if "market" in p["tags"])}

    # 5. which signals helped and which misled -> weights
    sigs = signal_audit(Cn)
    review["signals"] = sigs
    log_texts = []
    if settings.get("learning.enabled", True):
        factors, txt, logged = apply_factors(conn, sigs, settings.get("learning") or {}, today,
                                             prev.get("factors_logged"))
        review["factors"] = factors
        log_texts += [("weights", t) for t in txt]
    else:
        logged = prev.get("factors_logged") or {}

    # 6. situations where its recommendations lose -> lessons (confirmed on history before they change anything)
    revoked = set(prev.get("revoked", [])) if prev.get("model_id") == mid else set()
    was = {lz["key"]: lz["status"] for lz in prev.get("lessons", [])}
    lessons, cond_rows = [], []
    for key in list(CONDITIONS) + [k2 for k2 in LIVE_ONLY if "c_" + k2 in Cn]:
        c = Cn["c_" + key]
        w = _welch(Cn.loc[c == 1, "rx21"], Cn.loc[c == 0, "rx21"])
        if w["n_a"] < 5 or w["diff"] is None:
            continue
        hist = key in CONDITIONS
        row = {"key": key, "text": cond_text(key), "hist": hist, "n": w["n_a"], "n_rest": w["n_b"],
               "with": w["a"], "rest": w["b"], "diff": w["diff"], "t": w["t"]}
        cond_rows.append(row)
        t = w["t"] or 0
        strong = w["n_a"] >= MIN_IDEAS and w["diff"] <= -0.02 and t <= -2
        # an adopted lesson stays until its evidence clearly fades (no switching on and off around the threshold)
        kept = was.get(key) in ("adopted", "adopted_live") and w["n_a"] >= MIN_IDEAS and w["diff"] < 0 and t <= -1
        if strong or kept:
            test = tests.get(key) if hist else None
            if key in revoked:
                status = "revoked"
            elif not hist:
                status = "adopted_live" if w["n_a"] >= 40 and t <= -3 else "watch"
            elif test is None:
                status = "pending"
            else:
                status = "adopted" if test.get("passed") else "rejected"
            lessons.append({**row, "status": status, "test": test})
        elif w["n_a"] >= MIN_IDEAS and w["diff"] >= 0.02 and t >= 2:
            lessons.append({**row, "status": "strength", "test": tests.get(key) if hist else None})
    review["conditions"] = sorted(cond_rows, key=lambda r: r["diff"])

    # 7. did the filters skip stocks that then did better than the picks? (also the check of adopted lessons)
    filt = []
    pick_rx = P["rx21"]
    wn = N["why_not"].fillna("unknown")
    for why in ("upside", "risk", "edge", "deal", "band", "sector", "rank"):
        g = N[wn == why]
        if len(g) < 5:
            continue
        w = _welch(g["rx21"], pick_rx)
        filt.append({"key": why, "text": WHY_NOT_HE[why], "n": w["n_a"], "skipped": w["a"], "picks": w["b"],
                     "diff": w["diff"], "t": w["t"]})
    for lz in lessons:
        g = N[wn == "lesson:" + lz["key"]]
        if lz["status"] in ("adopted", "adopted_live") and len(g) >= 15:
            w = _welch(g["rx21"], pick_rx)
            if (w["diff"] or 0) >= 0.02 and (w["t"] or 0) >= 2:   # what it skipped did better: the lesson was wrong
                lz["status"] = "revoked"
                revoked.add(lz["key"])
    review["filters"] = filt
    review["lessons"] = lessons

    # 8. drift: the current model live vs. in its test
    drift = {"status": "none", "n": 0}
    if mid:
        Pm = P[P["model"] == mid]
        s = _mt(Pm["rx21"])
        drift = {"status": "none", "n": s["n"], "edge": s["mean"], "t": s["t"],
                 "test_edge": ((model.meta.get("validation") or {}).get("edge_fin"))}
        if s["n"] >= MIN_IDEAS:
            drift["status"] = ("bad" if (s["mean"] or 0) < 0 and (s["t"] or 0) <= -2
                               else "watch" if (s["mean"] or 0) < 0 else "ok")
    review["drift"] = drift

    reasons = []
    if drift["status"] == "bad":
        reasons.append("drift")
    if any(lz["status"] == "pending" for lz in lessons):
        reasons.append("lessons")
    review["retrain_request"] = {"date": today, "reasons": reasons} if reasons else None

    state = {"model_id": mid, "thresholds": thr, "drift": drift["status"], "bucket_live": bucket_live,
             "lessons": [{"key": lz["key"], "status": lz["status"], "text": lz["text"]} for lz in lessons],
             "revoked": sorted(revoked), "factors_logged": logged}
    log_texts += _changes(prev, state)
    review["conclusions"] = conclusions(review)
    review["log"] = _save_state(conn, prev, state, today, log_texts)
    return review


def _changes(prev: dict, state: dict) -> list[tuple[str, str]]:
    """What the review changed since the last scan (shown in the app as 'what I changed and when')."""
    out = []
    old = {lz["key"]: lz["status"] for lz in prev.get("lessons", [])}
    for lz in state["lessons"]:
        st, was = lz["status"], old.get(lz["key"])
        if st == was:
            continue
        if st == "adopted":
            out.append(("lesson", f"לקח חדש אומץ: לא לבחור {lz['text']}. זה נמצא בתוצאות בפועל ואושר גם ב־5 שנות היסטוריה"))
        elif st == "adopted_live":
            out.append(("lesson", f"לקח חדש אומץ: לא לבחור {lz['text']} (לפי התוצאות בפועל בלבד, אין היסטוריה לבדוק)"))
        elif st == "rejected":
            out.append(("lesson", f"חשד ל{lz['text']} נבדק ב־5 שנות היסטוריה ולא החזיק – כנראה מזל רע, לא שיניתי דבר"))
        elif st == "pending":
            out.append(("lesson", f"חשד: {lz['text']} מפסידות אצלי. ייבדק על 5 שנות היסטוריה באימון הבא"))
        elif st == "revoked":
            out.append(("lesson", f"ביטלתי את הלקח על {lz['text']}: המניות שדילגתי עליהן בגללו עשו יותר מהבחירות"))
    for key, was in old.items():
        if was in ("adopted", "adopted_live") and key not in {lz["key"] for lz in state["lessons"]}:
            out.append(("lesson", f"הלקח על {cond_text(key)} כבר לא נתמך בתוצאות – הפסקתי להשתמש בו"))
    if state["drift"] != prev.get("drift"):
        if state["drift"] == "bad":
            out.append(("model", "המודל מצליח בפועל פחות מהמניות שהוא דוחה. הקטנתי את המשקל שלו בחצי וביקשתי אימון מחדש"))
        elif prev.get("drift") == "bad":
            out.append(("model", "המודל חזר לבחור טוב מהממוצע – המשקל שלו חזר לרגיל"))
    if state.get("model_id") and prev.get("model_id") and state["model_id"] != prev["model_id"]:
        out.append(("model", "אומן מודל חדש: הבדיקה של המודל החדש מתחילה מאפס"))
    return out


def _save_state(conn, prev: dict, state: dict, today: date, log_texts: list[tuple[str, str]]) -> list[dict]:
    conn.execute("INSERT OR REPLACE INTO kv VALUES ('review_state', ?)", (json.dumps(state, ensure_ascii=False),))
    for kind, text in log_texts:
        conn.execute("INSERT INTO review_log (date, kind, text) VALUES (?,?,?)", (today.isoformat(), kind, text))
    conn.commit()
    cur = conn.execute("SELECT date, kind, text FROM review_log ORDER BY id DESC LIMIT 40")
    return [{"date": d, "kind": k, "text": t} for d, k, t in cur]


def conclusions(rv: dict) -> list[dict]:
    """The review in plain Hebrew, most important first."""
    out = []
    add = lambda tone, text: out.append({"tone": tone, "text": text})  # noqa: E731
    c, s, fin = rv.get("counts") or {}, rv.get("score") or {}, rv.get("final") or {}
    if not c.get("graded"):
        nxt = c.get("next_check")
        add("info", f"עוד לא עבר מספיק זמן לבדיקה: כל המלצה נבדקת אחרי 21 ימי מסחר (בערך חודש)"
                    f"{f', הראשונה בסביבות {nxt:%d/%m}' if isinstance(nxt, date) else ''}. עד עכשיו נרשמו "
                    f"{c.get('ideas', 0)} מניות שבדקתי, מתוכן {c.get('pick_ideas', 0)} שבחרתי, עם כל מה שחזיתי עליהן.")
    if s.get("n"):
        txt = (f"חודש אחרי ההמלצה: המניות שבחרתי {_f(s['pick_ret'])} בממוצע, המניות שבדקתי ולא בחרתי "
               f"{_f(s['other_ret'])}" + (f", S&P 500 {_f(s['pick_spy'])}" if s.get("pick_spy") is not None else "")
               + f" ({s['n']} בחירות).")
        v = s.get("verdict")
        if v == "good":
            add("good", txt + " הבחירה שלי מוסיפה ערך מעבר למקרה.")
        elif v == "bad":
            add("bad", txt + " הבחירה שלי גרועה מהמניות שדחיתי – משהו בשיטה לא עובד כרגע.")
        elif v == "unclear":
            add("info", txt + " ההבדל עוד קטן מדי כדי לדעת אם זה כישרון או מזל.")
        else:
            add("info", txt + f" זה עוד מעט מדי כדי לשפוט (צריך לפחות {MIN_IDEAS}).")
    if fin.get("n"):
        add("good" if (fin.get("ret") or 0) > 0 else "warn",
            f"{fin['n']} עסקאות של התוכנית כבר נסגרו: {fin['target']} ביעד, {fin['stop']} בסטופ, {fin['time']} בסוף "
            f"3 החודשים. עסקה ממוצעת {_f(fin.get('ret'))}.")
    cal = rv.get("calibration") or {}
    if cal.get("n", 0) >= 30 and cal.get("prob") is not None:
        gap = cal["actual"] - cal["prob"]
        fix = "מעכשיו הסיכוי שמוצג מתוקן לפי מה שקרה בפועל, בכל רמת סיכוי שיש לה מספיק תוצאות."
        tail = ("הסיכוי שהראיתי היה מדויק." if abs(gap) <= 0.05 else
                "הייתי אופטימי מדי. " + fix if gap < 0 else "הייתי פסימי מדי. " + fix)
        add("good" if abs(gap) <= 0.05 else "warn",
            f"כשהראיתי בממוצע סיכוי של {pct(cal['prob'])} להגיע ליעד, בפועל {pct(cal['actual'])} הגיעו ({cal['n']} מניות). {tail}")
    lc = rv.get("loss_causes") or {}
    if lc.get("n", 0) >= 5:
        parts = [f"{CAUSE_HE.get(k, k)} – {v}" for k, v in lc["by"].items()]
        add("info", f"למה הפסדתי ({lc['n']} עסקאות): " + "; ".join(parts) + ".")
        if lc.get("reversal_any", 0) >= max(3, 0.3 * lc["n"]):
            add("warn", f"ב־{lc['reversal_any']} מההפסדים המניה הייתה קודם ברווח יפה ואז התהפכה. "
                        "כלומר הכיוון היה נכון, אבל היציאה לא שמרה על הרווח.")
        if lc.get("market_any", 0) >= max(3, 0.4 * lc["n"]):
            add("warn", f"{lc['market_any']} מההפסדים קרו כשהשוק כולו ירד. בחירת המניות לא הייתה הבעיה העיקרית – התזמון היה.")
    sig = rv.get("signals") or []
    good = [x["label"] for x in sig if x["verdict"] == "helped"]
    bad = [x["label"] for x in sig if x["verdict"] == "misled"]
    if good:
        add("good", "צדקתי כשהסתמכתי על: " + ", ".join(good[:4]) + ". המשקל שלהם בציון עולה.")
    if bad:
        add("bad", "טעיתי כשהסתמכתי על: " + ", ".join(bad[:4]) + ". המשקל שלהם בציון יורד.")
    for lz in rv.get("lessons") or []:
        st = lz["status"]
        num_txt = f"{_f(lz['with'])} לעומת {_f(lz['rest'])} בשאר ({lz['n']} מניות)"
        if st == "adopted":
            tst = (lz.get("test") or {}).get("sel") or {}
            add("good", f"למדתי: לא לבחור {lz['text']}. בפועל הן עשו {num_txt}, וגם ב־5 שנות היסטוריה דילוג עליהן "
                        f"שיפר את הבחירות ({_f(tst.get('delta'), 2)} לעסקה). מעכשיו הן לא נבחרות.")
        elif st == "adopted_live":
            add("good", f"למדתי: לא לבחור {lz['text']}. בפועל הן עשו {num_txt}. זה לפי התוצאות בפועל בלבד.")
        elif st == "pending":
            add("warn", f"חשד: {lz['text']} עשו אצלי {num_txt}. לפני שאשנה משהו, זה ייבדק על 5 שנות היסטוריה באימון הבא.")
        elif st == "rejected":
            add("info", f"{lz['text']} עשו אצלי {num_txt}, אבל ב־5 שנות היסטוריה דילוג עליהן לא היה עוזר. "
                        "כנראה מזל רע ולא דפוס, ולכן לא שיניתי דבר.")
        elif st == "watch":
            add("info", f"תצפית: {lz['text']} עשו אצלי {num_txt}. עוד מעט מדי נתונים כדי לשנות משהו.")
        elif st == "revoked":
            add("info", f"ביטלתי את הלקח על {lz['text']}: המניות שדילגתי עליהן בגללו עשו יותר מהבחירות.")
        elif st == "strength":
            add("good", f"צדקתי במיוחד ב{lz['text']}: {num_txt}.")
    works = []
    for fl in rv.get("filters") or []:
        if fl["n"] >= 15 and (fl["diff"] or 0) >= 0.02 and (fl["t"] or 0) >= 2:
            add("warn", f"מניות שלא בחרתי כי {fl['text']} עשו {_f(fl['skipped'])} לעומת {_f(fl['picks'])} לבחירות "
                        f"({fl['n']} מניות). ייתכן שהמסנן הזה מחמיר מדי.")
        elif fl["n"] >= 15 and (fl["diff"] or 0) <= -0.02 and (fl["t"] or 0) <= -2:
            works.append(fl["text"])
    if works:
        add("good", "הסינון עובד: מניות שלא בחרתי כי " + " או כי ".join(works[:2])
            + " עשו אחר כך פחות מהמניות שבחרתי.")
    dr = rv.get("drift") or {}
    if dr.get("status") == "bad":
        add("bad", f"המודל הנוכחי בוחר בפועל גרוע מהמניות שהוא דוחה ({_f(dr.get('edge'))} לבחירה, {dr['n']} בחירות). "
                   "הקטנתי את המשקל שלו בחצי וביקשתי אימון מחדש על הנתונים העדכניים.")
    elif dr.get("status") == "watch":
        add("warn", f"המודל הנוכחי עוד לא מראה יתרון בפועל ({_f(dr.get('edge'))} לבחירה, {dr['n']} בחירות). ממשיך לעקוב.")
    elif dr.get("status") == "ok":
        add("good", f"המודל הנוכחי בוחר בפועל טוב מהמניות שהוא דוחה ({_f(dr.get('edge'))} לבחירה, {dr['n']} בחירות).")
    return out


# ------------------------------------------------------------------ used by the daily scan
def live_bucket(state: dict, mid: str | None, bucket: int | None, hist: dict | None) -> dict | None:
    """What really happened so far to recommendations at this probability level (current model only), and the
    test result corrected by it (shrunk towards the test: PRIOR_N live trades weigh as much as the test)."""
    if not mid or bucket is None or state.get("model_id") != mid:
        return None
    b = (state.get("bucket_live") or {}).get(str(int(bucket)))
    if not b or b[0] < 10:
        return None
    n, hits, ret_sum = b
    out = {"n": n, "rate": hits / n, "ret": ret_sum / n}
    if hist and hist.get("ret") is not None:
        out["ret_adj"] = (hist["ret"] * PRIOR_N + ret_sum) / (PRIOR_N + n)
    if hist and hist.get("rate") is not None:
        out["rate_adj"] = (hist["rate"] * PRIOR_N + hits) / (PRIOR_N + n)
    return out


def active_lessons(state: dict) -> list[dict]:
    return [lz for lz in state.get("lessons", []) if lz.get("status") in ("adopted", "adopted_live")]


def brief(rv: dict | None) -> dict | None:
    """The few numbers the 'today' screen shows."""
    if not rv:
        return None
    s, c = rv.get("score") or {}, rv.get("counts") or {}
    return {"graded": c.get("graded_picks", 0), "edge": s.get("edge"), "verdict": s.get("verdict"),
            "pick_ret": s.get("pick_ret"), "pick_spy": s.get("pick_spy"),
            "drift": (rv.get("drift") or {}).get("status"),
            "lessons": sum(1 for lz in rv.get("lessons") or [] if lz["status"] in ("adopted", "adopted_live")),
            "next_check": c.get("next_check")}
