"""Train and honestly test the probability model on years of real history.

Question the model answers (the app's own trading plan): "of stocks that looked like this on day t, how many
reached the target (3 x the distance to a 2.5 x ATR stop) before the stop, within 63 trading days?"
Because both the stop and the target scale with each stock's own volatility, wild stocks get no free
advantage: the model has to find stocks that rise more than their usual swings explain.

Data, all point-in-time: daily prices (Yahoo), every insider trade by SEC filing date (SEC quarterly data sets),
earnings report dates (SEC 8-K item 2.02) with the market's reaction and, when Yahoo has it, the EPS surprise.
Testing is walk-forward: the model is fitted on older years and scored on later years it never saw, with a
3-month gap so no outcome leaks into the test. Every simulated trade is also measured in money (target, stop
or time exit, gaps, costs) and compared with all stocks, the old technical screen and the S&P 500."""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .features import (EARN, FEATURES, INDUSTRY, INSIDER, LABELS_HE, IndustryPeers, InsiderArrays, earnings_matrix,
                       market_at, tech_frame)
from .journal import spearman
from .model import (GBM, KIND_HE, ProbModel, Transform, auc, build_meta, fit_logistic, fit_ridge, logit, model_spec,
                    sigmoid)
from .signals.technical import compute_panel_features, eligibility, scores_at
from .sources import get_sources

log = logging.getLogger("radar.train")

# conditions for the "what worked in history" list shown in the app: (feature, Hebrew text, test)
_RANKED = {  # continuous features: compared within each day (top / bottom 20% of all stocks that day)
    "mom_6_1": ("מומנטום חזק בחצי השנה האחרונה (20% החזקות)", "מומנטום חלש בחצי השנה האחרונה (20% החלשות)"),
    "rs_63": ("חזקה מה־S&P 500 בשלושה חודשים (20% החזקות)", "חלשה מה־S&P 500 בשלושה חודשים (20% החלשות)"),
    "ret_21": ("עלתה חזק בחודש האחרון (20% העליונות)", "ירדה בחודש האחרון (20% התחתונות)"),
    "log_vol_ratio": ("מחזור מסחר חריג בשבוע האחרון (20% העליונות)", "מחזור מסחר נמוך מהרגיל (20% התחתונות)"),
    "dist_high": ("קרובה לשיא השנתי (20% הקרובות)", "רחוקה מהשיא השנתי (20% הרחוקות)"),
    "squeeze": ("תנודה רחבה מהרגיל (20% העליונות)", "התכווצות תנודה (20% המכווצות)"),
    "atr_pct": ("תנודתיות יומית גבוהה (20% התנודתיות)", "תנודתיות יומית נמוכה (20% הרגועות)"),
    "rsi14": ("RSI גבוה – קנויה (20% העליונות)", "RSI נמוך – מכורה (20% התחתונות)"),
    "log_dollar_vol": ("נזילות גבוהה (20% הנזילות)", "נזילות נמוכה (20% הפחות נזילות)"),
    "log_price": ("מחיר מניה גבוה (20% היקרות)", "מחיר מניה נמוך (20% הזולות)"),
}
_FIXED = [
    ("ins_buy", "מנהלים קנו מניות בשוק הפתוח ב־90 הימים האחרונים", lambda d: d["ins_buy"] == 1),
    ("ins_top_exec", "המנכ\"ל או סמנכ\"ל הכספים קנה מניות", lambda d: d["ins_top_exec"] == 1),
    ("ins_n_buyers", "2 מנהלים או יותר קנו (קנייה בקבוצה)", lambda d: d["ins_n_buyers"] >= 2),
    ("ins_log_value", "קניות מנהלים של 250 אלף $ ומעלה", lambda d: d["ins_log_value"] >= math.log10(26)),
    ("ins_hold_incr", "מנהל הגדיל את ההחזקה שלו ב־10% ומעלה", lambda d: d["ins_hold_incr"] >= 0.1),
    ("ins_net_sell", "מכירות כבדות של כמה מנהלים", lambda d: d["ins_net_sell"] == 1),
    ("earn_reaction", "קפצה 5% ומעלה מעל השוק בתגובה לדוח רבעוני", lambda d: (d["earn_recent"] == 1) & (d["earn_reaction"] >= 0.05)),
    ("earn_reaction", "ירדה 5% ומעלה מתחת לשוק בתגובה לדוח רבעוני", lambda d: (d["earn_recent"] == 1) & (d["earn_reaction"] <= -0.05)),
    ("earn_surprise", "הרווח בדוח עקף את התחזיות ב־10% ומעלה", lambda d: (d["earn_recent"] == 1) & (d["earn_surprise"] >= 0.10)),
    ("earn_surprise", "הרווח בדוח פספס את התחזיות", lambda d: (d["earn_recent"] == 1) & (d["earn_surprise"] < 0)),
    ("above_50", "מעל הממוצע של 50 ימים", lambda d: d["above_50"] == 1),
    ("above_50", "מתחת לממוצע של 50 ימים", lambda d: d["above_50"] == 0),
    ("trend_50_200", "מגמה עולה (ממוצע 50 מעל ממוצע 200)", lambda d: d["trend_50_200"] == 1),
    ("mkt_above_200", "כשהשוק כולו במגמה עולה", lambda d: d["mkt_above_200"] == 1),
    ("mkt_above_200", "כשהשוק כולו במגמה יורדת", lambda d: d["mkt_above_200"] == 0),
]


def _sample(tickers: list[str], max_n: int) -> list[str]:
    if not max_n or len(tickers) <= max_n:
        return tickers
    step = len(tickers) / max_n  # spread across the list (SEC orders roughly by size): big and small stocks
    return [tickers[int(i * step)] for i in range(max_n)]


def simulate_trades(O: np.ndarray, H: np.ndarray, L: np.ndarray, C: np.ndarray, atr: np.ndarray, positions,
                    window: int, stop_atr: float, target_r: float | None = None, target_pct: float | None = None,
                    cost: float | np.ndarray = 0.002, spy_open: np.ndarray | None = None,
                    spy_close: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Replays the app's trading plan for every stock on every snapshot day (arrays: snapshots x stocks).

    Buy at the next day's open. Stop = entry - stop_atr x ATR. Target = entry + target_r x (entry - stop),
    or entry x (1 + target_pct). If neither is hit within `window` days, sell at that day's close.
    A gap through a level fills at the open; a day that touches both levels counts as the stop.
    Returns y (1 = target first), ret (after costs), r (result in multiples of the risk), days,
    kind (1 target, -1 stop, 0 time) and spy (S&P 500 over the same days). NaN = cannot be judged."""
    P, N = len(positions), O.shape[1]
    out = {k: np.full((P, N), np.nan) for k in ("y", "ret", "r", "days", "kind", "spy", "cost")}
    cols = np.arange(N)
    with np.errstate(invalid="ignore", divide="ignore"):
        for i, p in enumerate(positions):
            if p + window >= len(O):
                continue
            e, a = O[p + 1], atr[p]
            risk = stop_atr * a
            stop = e - risk
            tgt = e + target_r * risk if target_r else e * (1 + float(target_pct))
            sl = slice(p + 1, p + 1 + window)
            hw, lw, ow = H[sl], L[sl], O[sl]
            ht, hs = hw >= tgt, lw <= stop
            ft = np.where(ht.any(0), ht.argmax(0), window)
            fs = np.where(hs.any(0), hs.argmax(0), window)
            hit_t = ft < fs                                   # same day as the stop -> counted as the stop
            hit_s = (fs <= ft) & (fs < window)
            ftc, fsc = np.minimum(ft, window - 1), np.minimum(fs, window - 1)
            o_t, o_s = ow[ftc, cols], ow[fsc, cols]
            px_t = np.where(o_t >= tgt, o_t, tgt)             # opened above the target: sold at the open
            px_s = np.where((fsc > 0) & (o_s <= stop), o_s, stop)  # opened below the stop: sold at the open
            last = pd.DataFrame(C[sl]).ffill().to_numpy()[-1]
            exit_px = np.where(hit_t, px_t, np.where(hit_s, px_s, last))
            held = np.where(hit_t, ftc + 1, np.where(hit_s, fsc + 1, window)).astype(int)
            c = cost[i] if np.ndim(cost) == 2 else cost   # one cost per stock (spread depends on liquidity)
            ret = exit_px / e - 1 - c
            bad = np.isnan(e) | np.isnan(a) | ~(a > 0) | ~(stop > 0) | np.isnan(exit_px)
            row = {"cost": np.broadcast_to(np.asarray(c, dtype=float), ret.shape), "y": hit_t.astype(float), "ret": ret, "r": ret * e / risk, "days": held.astype(float),
                   "kind": np.where(hit_t, 1.0, np.where(hit_s, -1.0, 0.0))}
            if spy_open is not None and spy_close is not None:
                row["spy"] = spy_close[np.minimum(p + held, len(spy_close) - 1)] / spy_open[p + 1] - 1
            for k, v in row.items():
                v = np.asarray(v, dtype=float).copy()
                v[bad] = np.nan
                out[k][i] = v
    return out


def trade_costs(dollar_volume: np.ndarray, base: float) -> np.ndarray:
    """Round-trip cost per trade: commissions/slippage (base) plus a bid-ask spread that grows as liquidity falls,
    from 0 for stocks trading $50M+ a day to 0.5% at $5M a day (and more below)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        lv = np.log10(np.where(dollar_volume > 0, dollar_volume, np.nan))
    extra = np.clip(0.005 * (np.log10(5e7) - lv), 0, 0.01)
    return base + np.nan_to_num(extra, nan=0.005)


def trade_stats(df: pd.DataFrame) -> dict | None:
    """Average result of a set of simulated trades."""
    if df is None or not len(df):
        return None
    ret = df["ret"].to_numpy(dtype=float)
    spy = df["spy"].to_numpy(dtype=float) if "spy" in df else np.full(len(df), np.nan)
    out = {"n": int(len(df)), "hit": float(df["y"].mean()), "ret": float(np.nanmean(ret)),
           "med": float(np.nanmedian(ret)), "win": float(np.nanmean(ret > 0)),
           "stop": float((df["kind"] == -1).mean()), "target": float((df["kind"] == 1).mean()),
           "r": float(df["r"].mean()), "days": float(df["days"].mean())}
    if "cost" in df:  # sensitivity: what if trading costs were twice as high?
        out["cost"] = float(df["cost"].mean())
        out["ret_2x_cost"] = float(np.nanmean(ret - df["cost"].to_numpy(dtype=float)))
    if np.isfinite(spy).any():
        out["spy"] = float(np.nanmean(spy))
        out["excess"] = float(np.nanmean(ret - spy))
    return out


def _top(df: pd.DataFrame, col: str, n: int) -> pd.DataFrame:
    return df.sort_values(col, ascending=False).groupby("pos", sort=False).head(n)


def _what_worked(D: pd.DataFrame, min_n: int) -> list[dict]:
    y, base = D["y"].to_numpy(), float(D["y"].mean())
    ret = D["ret"].to_numpy(dtype=float)
    out = []
    for key, (hi_txt, lo_txt) in _RANKED.items():
        r = D.groupby("pos")[key].rank(pct=True)
        for txt, m in ((hi_txt, r >= 0.8), (lo_txt, r <= 0.2)):
            m = m.fillna(False).to_numpy()
            if m.sum() >= min_n:
                out.append({"key": key, "text": txt, "rate": float(y[m].mean()), "ret": float(np.nanmean(ret[m])),
                            "n": int(m.sum())})
    for key, txt, fn in _FIXED:
        m = fn(D).fillna(False).to_numpy(dtype=bool)
        if m.sum() >= min_n:
            out.append({"key": key, "text": txt, "rate": float(y[m].mean()), "ret": float(np.nanmean(ret[m])),
                        "n": int(m.sum())})
    for o in out:
        o["lift"] = o["rate"] / base if base else None
    return sorted(out, key=lambda o: -(o["lift"] or 0))


def _top_rate(df: pd.DataFrame, col: str, n: int) -> tuple[float | None, int]:
    top = _top(df, col, n)
    return (float(top["y"].mean()) if len(top) else None), len(top)


def _events_for(src, cols, meta_u, since, cfg, progress_every=100):
    """Earnings events for every training stock: SEC dates for all, Yahoo surprises within a time budget."""
    budget = float(cfg.get("yahoo_minutes", 20)) * 60
    max_yahoo = int(cfg.get("earnings_yahoo_max", 800))
    http = getattr(src, "train_http", None)
    t0 = time.monotonic()
    state = {"yahoo_tries": 0, "yahoo_hits": 0, "done": 0}

    def one(i_t):
        i, t = i_t
        use_y = (i < max_yahoo and time.monotonic() - t0 < budget
                 and not (state["yahoo_tries"] >= 40 and state["yahoo_hits"] == 0))  # Yahoo blocked: stop asking
        cik = (meta_u.get(t) or {}).get("cik")
        try:
            ev = src.earnings_events(t, cik, None, since, yahoo=use_y, http=http)
        except Exception as e:  # noqa: BLE001
            log.debug("earnings events failed %s: %s", t, e)
            ev = None
        sic = src.company_sic(cik, http) if cik and hasattr(src, "company_sic") else None
        if use_y:
            state["yahoo_tries"] += 1
            if ev is not None and len(ev) and ev["surprise"].notna().any():
                state["yahoo_hits"] += 1
        state["done"] += 1
        if state["done"] % progress_every == 0:
            log.info("תאריכי דוחות רבעוניים: %d/%d מניות", state["done"], len(cols))
        return t, ev, sic

    workers = int(cfg.get("workers", 4))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        out = list(ex.map(one, list(enumerate(cols))))
    state["sic"] = {t: s for t, _e, s in out if s}
    return {t: e for t, e, _s in out}, state


def train_model(settings, demo: bool = False, out_path: Path | None = None) -> dict:
    cfg, filters = settings.get("train"), settings.get("filters")
    years, W = int(cfg["years"]), int(cfg["horizon_days"])
    k = float(settings.get("risk.atr_stop_multiple"))
    mode = str(cfg.get("target_mode", "r")).lower()
    target_r = float(settings.get("risk.target_r", 3.0)) if mode != "pct" else None
    target_pct = float(cfg["target"]) if mode == "pct" else None
    cost = float(cfg.get("cost_pct", 0.2)) / 100
    step, top_n = int(cfg["step_days"]), int(settings.get("scan.max_picks") or 10)
    src = get_sources(settings, demo)

    # 1. prices --------------------------------------------------------------------
    universe = src.universe()
    meta_u = {u["ticker"]: u for u in universe}
    tickers = _sample([u["ticker"] for u in universe], int(cfg.get("max_tickers") or 0))
    log.info("אימון המודל: מוריד %d שנות מחירים עבור %d מניות...", years + 1, len(tickers))
    panel = src.prices(sorted(set(tickers) | {"SPY"}), f"{years + 1}y", use_cache=False)
    C = panel["Close"]
    if C.empty or len(C) < 252 + W + 60:
        raise RuntimeError("לא התקבלו מספיק נתוני מחירים לאימון המודל.")
    f = compute_panel_features(panel)
    elig = eligibility(f, filters)
    dates = C.index
    cols = [t for t in C.columns if t != "SPY"]
    spy = C["SPY"] if "SPY" in C.columns else None

    # 2. insider history (SEC) -------------------------------------------------------
    ciks = {int(meta_u[t]["cik"]) for t in cols if (meta_u.get(t) or {}).get("cik")}
    # one extra year, so 'first buy in a year' is known from the first training day on
    log.info("מוריד את כל דיווחי המנהלים מ־SEC ל־%d שנים (קובץ לכל רבעון)...", years + 1)
    ins = src.insider_history(years + 1, ciks, lambda q, n: log.info("SEC רבעון %s: %d עסקאות רלוונטיות", q, n))
    have_ins = ins is not None and not ins.empty
    ins_start = min(d for d in ins["filing_date"] if d is not None).toordinal() if have_ins else None
    by_cik = {int(c): InsiderArrays(g, history_start=ins_start) for c, g in ins.groupby("cik")} if have_ins else {}
    last_ins = max(ins["filing_date"]) if have_ins else None
    if not have_ins:
        log.warning("לא התקבלו נתוני מנהלים היסטוריים מ־SEC – המודל יתאמן בלעדיהם")

    # 3. snapshot days -----------------------------------------------------------------
    start, end = 252, len(dates) - W - 2
    if last_ins is not None:  # the newest SEC quarter may not be published yet: stop where the data stops
        end = min(end, int(dates.searchsorted(pd.Timestamp(last_ins), "right")) - 1)
    positions = list(range(start, end + 1, step))
    if len(positions) < 30:
        raise RuntimeError("אין מספיק היסטוריה לאימון המודל.")

    # 4. earnings history ----------------------------------------------------------------
    since = (dates[start] - pd.Timedelta(days=120)).date()
    log.info("אוסף תאריכי דוחות רבעוניים ותגובות השוק...")
    events, ystate = _events_for(src, cols, meta_u, since, cfg)

    # 5. labels + features ------------------------------------------------------------------
    log.info("מחשב תוצאות ומאפיינים ל־%d ימי דגימה...", len(positions))
    O = panel["Open"][cols].to_numpy(dtype=float)
    H = panel["High"][cols].to_numpy(dtype=float)
    L = panel["Low"][cols].to_numpy(dtype=float)
    atr = f["atr14"][cols].to_numpy(dtype=float)
    spy_o = panel["Open"]["SPY"].ffill().to_numpy(dtype=float) if spy is not None else None
    spy_c = spy.ffill().to_numpy(dtype=float) if spy is not None else None
    cost_m = trade_costs(f["dollar_vol20"][cols].to_numpy(dtype=float)[positions], cost)
    tr_ = simulate_trades(O, H, L, C[cols].to_numpy(dtype=float), atr, positions, W, k, target_r, target_pct, cost_m,
                          spy_o, spy_c)
    Y = tr_["y"]
    weights = settings.get("weights")
    peers = IndustryPeers(ystate.get("sic"))
    targets = {t: peers.sic.get(t) for t in cols}
    parts = []
    for i, p in enumerate(positions):
        t = dates[p]
        ok = elig.loc[t, cols].to_numpy(dtype=bool) & ~np.isnan(Y[i])
        if not ok.any():
            continue
        tf = tech_frame(f, t).reindex(cols)[ok]
        sc = scores_at(f, t, elig.loc[t], weights)
        tf["tech_score"] = sc["tech_score"].reindex(tf.index) if "tech_score" in sc else np.nan
        tf["pos"] = p
        for kk in ("y", "ret", "r", "days", "kind", "spy", "cost"):
            tf[kk] = tr_[kk][i][ok]
        for kk, v in (market_at(spy, p) if spy is not None else {}).items():
            tf[kk] = v
        ind = peers.frame(f["mom_6_1"].loc[t], f["rs_63"].loc[t], elig.loc[t], {k2: targets[k2] for k2 in tf.index})
        for kk in INDUSTRY:
            tf[kk] = ind[kk].reindex(tf.index).to_numpy()
        parts.append(tf)
    if not parts:
        raise RuntimeError("אף מניה לא עברה את מסנני הנזילות בתקופת האימון.")
    D = pd.concat(parts).rename_axis("ticker").reset_index()
    for kk in ("mkt_above_200", "mkt_ret_63"):
        if kk not in D:
            D[kk] = np.nan
    days = np.array([d.toordinal() for d in dates.date])
    ins_cols = {kk: np.zeros(len(D)) for kk in INSIDER}
    earn_cols = {kk: np.full(len(D), np.nan) for kk in EARN}
    spy_full = spy.dropna() if spy is not None else None
    for t, g in D.groupby("ticker").groups.items():
        rows = np.asarray(g)
        pos = D["pos"].to_numpy()[rows]
        cik = (meta_u.get(t) or {}).get("cik")
        arr = by_cik.get(int(cik)) if cik and have_ins else None
        if arr is not None:
            for j, r in enumerate(rows):
                for kk, v in arr.at(int(days[pos[j]])).items():
                    ins_cols[kk][r] = v
        s = C[t].dropna()
        spy_al = spy_full.reindex(s.index).ffill() if spy_full is not None else None
        tpos = s.index.searchsorted(dates[pos], "right") - 1
        em = earnings_matrix(events.get(t), s, spy_al, tpos)
        for kk in EARN:
            earn_cols[kk][rows] = em[kk]
    for kk in INSIDER:
        D[kk] = ins_cols[kk] if have_ins else np.nan
    for kk in EARN:
        D[kk] = earn_cols[kk]
    D = D.sort_values(["pos", "ticker"]).reset_index(drop=True)
    X, y, pos = D[FEATURES].to_numpy(dtype=float), D["y"].to_numpy(dtype=float), D["pos"].to_numpy()
    goal = f"פי {target_r:g} מהסטופ" if target_r else f"+{target_pct:.0%}"
    log.info("מאגר האימון: %d תצפיות, %d מניות, %d ימים. בסיס: %.1f%% הגיעו ליעד (%s) לפני הסטופ, "
             "עסקה ממוצעת %+.2f%%", len(D), D["ticker"].nunique(), len(positions), 100 * y.mean(), goal,
             100 * float(D["ret"].mean()))

    # money target for the money models: the trade's result relative to all stocks on the same day (the model only
    # has to rank stocks against each other; whether the whole market rises is not its question)
    ret_c = D["ret"].clip(-0.5, 1.0)
    D["rx"] = ret_c - ret_c.groupby(D["pos"]).transform("mean")
    rx = D["rx"].to_numpy(dtype=float)

    # 6. walk-forward test: every candidate model is fitted on older years and scored on later ones -----------
    upos = np.array(sorted(set(pos)))
    blocks = np.array_split(upos, int(cfg.get("folds", 5)))
    test_blocks = blocks[1:]
    half = max(1, len(test_blocks) // 2)
    sel_pos = np.concatenate(test_blocks[:half])       # period A: used to choose the model
    fin_pos = np.concatenate(test_blocks[half:])       # period B: final check, used for nothing else
    lam_k = float(cfg.get("l2", 1e-3))
    kinds = [k2 for k2 in cfg.get("candidates", ["hit", "lin", "gbm"]) if k2 in KIND_HE]
    oos = {k2: np.full(len(D), np.nan) for k2 in kinds}
    for b in test_blocks:
        tr = pos + W + 1 < b[0]                       # 3-month gap: training outcomes end before the test starts
        te = np.isin(pos, b)
        if tr.sum() < 2000 or y[tr].sum() < 50:
            continue
        for k2 in kinds:
            fit = _fit_candidate(k2, X[tr], y[tr], rx[tr], lam_k)
            oos[k2][te] = _predict_candidate(k2, fit, X[te])
    m = ~np.isnan(oos[kinds[0]])
    if m.sum() < 1000:
        raise RuntimeError("אין מספיק נתונים לבדיקה הוגנת של המודל.")
    if "hit" in oos:  # the hit model's scores become calibrated probabilities (fitted on its own test scores)
        cal = fit_logistic(np.column_stack([np.ones(m.sum()), logit(oos["hit"][m])]), y[m], lam=0.0)
        oos["hit"][m] = sigmoid(cal[0] + cal[1] * logit(oos["hit"][m]))
    else:
        cal = np.array([0.0, 1.0])
    T = D[m].copy()
    for k2 in kinds:
        T["s_" + k2] = oos[k2][m]
    A, B = T[T["pos"].isin(sel_pos)], T[T["pos"].isin(fin_pos)]
    tech_ok = lambda df: df.dropna(subset=["tech_score"])  # noqa: E731
    base_stats = {"sel": {"all": trade_stats(A), "tech": trade_stats(_top(tech_ok(A), "tech_score", top_n))},
                  "fin": {"all": trade_stats(B), "tech": trade_stats(_top(tech_ok(B), "tech_score", top_n))}}
    candidates = []
    for k2 in kinds:
        sa, sb = trade_stats(_top(A, "s_" + k2, top_n)) or {}, trade_stats(_top(B, "s_" + k2, top_n)) or {}
        candidates.append({"kind": k2, "label": KIND_HE[k2], "sel": sa, "fin": sb,
                           "ic": spearman(T["s_" + k2], T["ret"])})
        log.info("%s: תקופת הבחירה %+.2f%% לעסקה, בדיקה סופית %+.2f%% (כל המניות %+.2f%% / %+.2f%%)", KIND_HE[k2],
                 100 * (sa.get("ret") or 0), 100 * (sb.get("ret") or 0),
                 100 * (base_stats["sel"]["all"] or {}).get("ret", 0), 100 * (base_stats["fin"]["all"] or {}).get("ret", 0))
    chosen = max(candidates, key=lambda c: (c["sel"].get("ret") if c["sel"].get("ret") is not None else -9))
    kind = chosen["kind"]
    sc = "s_" + kind
    T["p"] = T[sc]
    sel_all, fin_all = base_stats["sel"]["all"] or {}, base_stats["fin"]["all"] or {}
    edge_sel = (chosen["sel"].get("ret") or 0) - (sel_all.get("ret") or 0)
    edge_fin = (chosen["fin"].get("ret") or 0) - (fin_all.get("ret") or 0)
    passed = bool(edge_sel > 0 and edge_fin > 0 and (chosen["fin"].get("ret") or 0) > 0)
    quality = float(min(1.0, max(0.25, edge_fin / 0.01))) if passed else 0.0

    Bp = T[T["pos"].isin(fin_pos)]
    base = float(Bp["y"].mean())
    top_rate, top_cnt = _top_rate(Bp, "p", top_n)
    q = pd.qcut(T["p"].rank(method="first"), 10, labels=False)
    deciles = []
    for i in range(10):
        g = T[q == i]
        st = trade_stats(g) or {}
        deciles.append({"p": float(g["p"].mean()), "p_lo": float(g["p"].min()), "p_hi": float(g["p"].max()),
                        "rate": float(g["y"].mean()), "ret": st.get("ret"), "r": st.get("r"),
                        "stop": st.get("stop"), "win": st.get("win"), "n": int(len(g))})
    T["year"] = dates[T["pos"].to_numpy()].year
    by_year = []
    for yr, g in T.groupby("year"):
        tp = trade_stats(_top(g, "p", top_n)) or {}
        al = trade_stats(g) or {}
        by_year.append({"year": int(yr), "top": tp.get("hit"), "base": al.get("hit"), "top_ret": tp.get("ret"),
                        "base_ret": al.get("ret"), "spy": al.get("spy"), "n": int(len(g))})
    a = auc(Bp["y"].to_numpy(), Bp["p"].to_numpy())
    pd_dates = lambda ps: (dates[int(min(ps))].date(), dates[int(max(ps))].date())  # noqa: E731
    sel_d, fin_d = pd_dates(sel_pos), pd_dates(fin_pos)
    validation = {
        "kind": kind, "auc": a, "base": base, "top_n": top_n, "top_rate": top_rate, "top_count": top_cnt,
        "top_lift": (top_rate / base) if top_rate is not None and base else None,
        "tech_top_rate": (base_stats["fin"]["tech"] or {}).get("hit"), "n_test": int(len(Bp)),
        "money": {"model": chosen["fin"], "tech": base_stats["fin"]["tech"], "all": base_stats["fin"]["all"]},
        "money_sel": {"model": chosen["sel"], "tech": base_stats["sel"]["tech"], "all": base_stats["sel"]["all"]},
        "money_all": trade_stats(T), "candidates": candidates, "edge_sel": edge_sel, "edge_fin": edge_fin,
        "passed": passed, "sel_start": sel_d[0], "sel_end": sel_d[1], "test_start": fin_d[0], "test_end": fin_d[1],
        "deciles": deciles, "by_year": by_year,
    }
    log.info("נבחר: %s. בדיקה סופית (%s עד %s): %d המובילות %+.2f%% לעסקה מול %+.2f%% לכל המניות. %s",
             KIND_HE[kind], fin_d[0], fin_d[1], top_n, 100 * (chosen["fin"].get("ret") or 0),
             100 * (fin_all.get("ret") or 0), "עבר את הבדיקה" if passed else "לא עבר את הבדיקה")

    # 7. the chosen model, fitted on everything ------------------------------------------------------------
    final = _fit_candidate(kind, X, y, rx, lam_k)
    n_tk = D["ticker"].nunique()
    coverage = {
        "insider": bool(have_ins), "insider_rows": float((D["ins_buy"] == 1).mean()) if have_ins else 0.0,
        "first_buy_rows": float((D["ins_first_buy"] == 1).mean()) if have_ins else 0.0,
        "industry_rows": float(D["ind_mom"].notna().mean()),
        "earnings_tickers": float(sum(1 for t in cols if events.get(t) is not None) / max(1, len(cols))),
        "surprise_rows": float(D["earn_surprise"].notna().mean() * (D["earn_recent"] == 1).mean()),
        "yahoo_tries": ystate["yahoo_tries"], "yahoo_hits": ystate["yahoo_hits"],
    }
    meta = build_meta(
        FEATURES, final.get("tf"), final.get("w"), kind=kind, gbm=final["gbm"].to_json() if "gbm" in final else None,
        demo=demo, years=years, spec=model_spec(settings), target_mode="pct" if target_pct else "r",
        target=target_pct, target_r=target_r, horizon=W, stop_atr=k, cost=cost, quality=quality,
        base_rate=float(T["y"].mean()), base_all=float(y.mean()), n_rows=len(D), n_tickers=n_tk,
        n_dates=len(positions), period={"start": dates[positions[0]].date(), "end": dates[positions[-1]].date()},
        calibration={"a": float(cal[0]), "b": float(cal[1])}, validation=validation,
        what_worked=_what_worked(D, max(100, len(D) // 400)), coverage=coverage,
        labels={k2: LABELS_HE[k2] for k2 in FEATURES}, industry_map=peers.sic,
    )
    model = ProbModel(meta)
    if out_path:
        model.save(Path(out_path))
    return meta


def _fit_candidate(kind: str, X: np.ndarray, y: np.ndarray, rx: np.ndarray, lam_k: float) -> dict:
    if kind == "hit":
        tfm = Transform.fit(X, FEATURES)
        return {"tf": tfm, "w": fit_logistic(tfm(X), y, lam=lam_k * len(y))}
    if kind == "lin":
        tfm = Transform.fit(X, FEATURES)
        return {"tf": tfm, "w": fit_ridge(tfm(X), rx, lam=0.005 * len(rx))}
    return {"gbm": GBM(min_leaf=max(200, len(rx) // 150)).fit(X, rx)}


def _predict_candidate(kind: str, fit: dict, X: np.ndarray) -> np.ndarray:
    if kind == "gbm":
        return fit["gbm"].predict(X)
    z = fit["tf"](X) @ fit["w"]
    return sigmoid(z) if kind == "hit" else z


def model_age_days(path: Path) -> float | None:
    """Days since the saved model was trained (None if there is no usable model)."""
    m = ProbModel.load(path)
    if m is None:
        return None
    try:
        trained = datetime.fromisoformat(str(m.meta["trained"]))
    except (KeyError, ValueError):
        return None
    if trained.tzinfo is None:
        trained = trained.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - trained) / timedelta(days=1)
