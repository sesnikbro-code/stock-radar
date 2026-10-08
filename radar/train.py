"""Train and honestly test the probability model on years of real history.

Question the model answers: "of stocks that looked like this on day t, how many rose +30% (from the next
day's open) before falling to a 2.5 x ATR stop, within 63 trading days (about 3 months)?"

Data, all point-in-time: daily prices (Yahoo), every insider trade by SEC filing date (SEC quarterly data sets),
earnings report dates (SEC 8-K item 2.02) with the market's reaction and, when Yahoo has it, the EPS surprise.
Testing is walk-forward: the model is fitted on older years and scored on later years it never saw,
with a 3-month gap so no outcome leaks from the training period into the test period."""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .features import EARN, FEATURES, INSIDER, LABELS_HE, InsiderArrays, earnings_matrix, market_at, tech_frame
from .model import ProbModel, Transform, auc, build_meta, fit_logistic, logit, sigmoid
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


def make_labels(O: np.ndarray, H: np.ndarray, L: np.ndarray, atr: np.ndarray, positions, window: int,
                target: float, stop_atr: float) -> np.ndarray:
    """1 = reached entry*(1+target) before entry - stop_atr*ATR within `window` days (entry = next open).
    A day that touches both counts as a loss. NaN = cannot be judged."""
    out = np.full((len(positions), O.shape[1]), np.nan)
    with np.errstate(invalid="ignore"):
        for i, p in enumerate(positions):
            entry, a = O[p + 1], atr[p]
            stop, tgt = entry - stop_atr * a, entry * (1 + target)
            hw, lw = H[p + 1:p + 1 + window], L[p + 1:p + 1 + window]
            ht, hs = hw >= tgt, lw <= stop
            ft = np.where(ht.any(0), ht.argmax(0), window + 1)
            fs = np.where(hs.any(0), hs.argmax(0), window + 1)
            y = ((ft < fs) & (ft < window)).astype(float)
            bad = np.isnan(entry) | np.isnan(a) | ~(a > 0) | ~(stop > 0) | (len(hw) < window)
            y[bad] = np.nan
            out[i] = y
    return out


def _what_worked(D: pd.DataFrame, min_n: int) -> list[dict]:
    y, base = D["y"].to_numpy(), float(D["y"].mean())
    out = []
    for key, (hi_txt, lo_txt) in _RANKED.items():
        r = D.groupby("pos")[key].rank(pct=True)
        for txt, m in ((hi_txt, r >= 0.8), (lo_txt, r <= 0.2)):
            m = m.fillna(False).to_numpy()
            if m.sum() >= min_n:
                out.append({"key": key, "text": txt, "rate": float(y[m].mean()), "n": int(m.sum())})
    for key, txt, fn in _FIXED:
        m = fn(D).fillna(False).to_numpy(dtype=bool)
        if m.sum() >= min_n:
            out.append({"key": key, "text": txt, "rate": float(y[m].mean()), "n": int(m.sum())})
    for o in out:
        o["lift"] = o["rate"] / base if base else None
    return sorted(out, key=lambda o: -(o["lift"] or 0))


def _top_rate(df: pd.DataFrame, col: str, n: int) -> tuple[float | None, int]:
    top = df.sort_values(col, ascending=False).groupby("pos", sort=False).head(n)
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
        if use_y:
            state["yahoo_tries"] += 1
            if ev is not None and len(ev) and ev["surprise"].notna().any():
                state["yahoo_hits"] += 1
        state["done"] += 1
        if state["done"] % progress_every == 0:
            log.info("תאריכי דוחות רבעוניים: %d/%d מניות", state["done"], len(cols))
        return t, ev

    workers = int(cfg.get("workers", 4))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        res = dict(ex.map(one, list(enumerate(cols))))
    return res, state


def train_model(settings, demo: bool = False, out_path: Path | None = None) -> dict:
    cfg, filters = settings.get("train"), settings.get("filters")
    years, W = int(cfg["years"]), int(cfg["horizon_days"])
    target, k = float(cfg["target"]), float(settings.get("risk.atr_stop_multiple"))
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
    log.info("מוריד את כל דיווחי המנהלים מ־SEC ל־%d שנים (קובץ לכל רבעון)...", years)
    ins = src.insider_history(years, ciks, lambda q, n: log.info("SEC רבעון %s: %d עסקאות רלוונטיות", q, n))
    have_ins = ins is not None and not ins.empty
    by_cik = {int(c): InsiderArrays(g) for c, g in ins.groupby("cik")} if have_ins else {}
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
    Y = make_labels(O, H, L, atr, positions, W, target, k)
    weights = settings.get("weights")
    parts = []
    for i, p in enumerate(positions):
        t = dates[p]
        ok = elig.loc[t, cols].fillna(False).to_numpy(bool) & ~np.isnan(Y[i])
        if not ok.any():
            continue
        tf = tech_frame(f, t).reindex(cols)[ok]
        sc = scores_at(f, t, elig.loc[t], weights)
        tf["tech_score"] = sc["tech_score"].reindex(tf.index) if "tech_score" in sc else np.nan
        tf["y"], tf["pos"] = Y[i][ok], p
        for kk, v in (market_at(spy, p) if spy is not None else {}).items():
            tf[kk] = v
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
    log.info("מאגר האימון: %d תצפיות, %d מניות, %d ימים. בסיס: %.1f%% הגיעו ל־+%d%% לפני הסטופ",
             len(D), D["ticker"].nunique(), len(positions), 100 * y.mean(), round(target * 100))

    # 6. walk-forward test ------------------------------------------------------------------
    upos = np.array(sorted(set(pos)))
    blocks = np.array_split(upos, int(cfg.get("folds", 5)))
    oos = np.full(len(D), np.nan)
    lam_k = float(cfg.get("l2", 1e-3))
    for b in blocks[1:]:
        tr = pos + W + 1 < b[0]                       # 3-month gap: training outcomes end before the test starts
        te = np.isin(pos, b)
        if tr.sum() < 2000 or y[tr].sum() < 50:
            continue
        tfm = Transform.fit(X[tr], FEATURES)
        w = fit_logistic(tfm(X[tr]), y[tr], lam=lam_k * tr.sum())
        oos[te] = sigmoid(tfm(X[te]) @ w)
    m = ~np.isnan(oos)
    if m.sum() < 1000:
        raise RuntimeError("אין מספיק נתונים לבדיקה הוגנת של המודל.")
    cal = fit_logistic(np.column_stack([np.ones(m.sum()), logit(oos[m])]), y[m], lam=0.0)
    p_cal = sigmoid(cal[0] + cal[1] * logit(oos[m]))
    T = D[m].copy()
    T["p"] = p_cal
    base = float(T["y"].mean())
    top_rate, top_cnt = _top_rate(T, "p", top_n)
    tech_rate, _ = _top_rate(T.dropna(subset=["tech_score"]), "tech_score", top_n)
    q = pd.qcut(T["p"].rank(method="first"), 10, labels=False)
    deciles = [{"p": float(T["p"][q == i].mean()), "rate": float(T["y"][q == i].mean()), "n": int((q == i).sum())}
               for i in range(10)]
    T["year"] = dates[T["pos"].to_numpy()].year
    by_year = []
    for yr, g in T.groupby("year"):
        r, _n = _top_rate(g, "p", top_n)
        by_year.append({"year": int(yr), "top": r, "base": float(g["y"].mean()), "n": int(len(g))})
    a = auc(T["y"].to_numpy(), T["p"].to_numpy())
    validation = {
        "auc": a, "base": base, "top_n": top_n, "top_rate": top_rate, "top_count": top_cnt,
        "top_lift": (top_rate / base) if top_rate is not None and base else None,
        "tech_top_rate": tech_rate, "n_test": int(m.sum()),
        "test_start": dates[int(T["pos"].min())].date(), "test_end": dates[int(T["pos"].max())].date(),
        "deciles": deciles, "by_year": by_year,
    }
    log.info("בדיקה על תקופה שהמודל לא ראה: AUC %.3f, %d המובילות %.1f%% מול ממוצע %.1f%% (הסינון הטכני: %s)",
             a or 0, top_n, 100 * (top_rate or 0), 100 * base,
             f"{100 * tech_rate:.1f}%" if tech_rate is not None else "-")

    # 7. final model on everything ------------------------------------------------------------
    tff = Transform.fit(X, FEATURES)
    w = fit_logistic(tff(X), y, lam=lam_k * len(y))
    n_tk = D["ticker"].nunique()
    coverage = {
        "insider": bool(have_ins), "insider_rows": float((D["ins_buy"] == 1).mean()) if have_ins else 0.0,
        "earnings_tickers": float(sum(1 for t in cols if events.get(t) is not None) / max(1, len(cols))),
        "surprise_rows": float(D["earn_surprise"].notna().mean() * (D["earn_recent"] == 1).mean()),
        "yahoo_tries": ystate["yahoo_tries"], "yahoo_hits": ystate["yahoo_hits"],
    }
    meta = build_meta(
        FEATURES, tff, w, demo=demo, years=years, target=target, horizon=W, stop_atr=k,
        base_rate=base, base_all=float(y.mean()), n_rows=len(D), n_tickers=n_tk, n_dates=len(positions),
        period={"start": dates[positions[0]].date(), "end": dates[positions[-1]].date()},
        calibration={"a": float(cal[0]), "b": float(cal[1])}, validation=validation,
        what_worked=_what_worked(D, max(100, len(D) // 400)), coverage=coverage,
        labels={k2: LABELS_HE[k2] for k2 in FEATURES},
    )
    model = ProbModel(meta)
    if out_path:
        model.save(Path(out_path))
    return meta


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
