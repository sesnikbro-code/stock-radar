"""Walk-forward backtest of the price/volume part of the system.

Honest by construction: scores at date t use only data up to t, entry is the NEXT day's open, stops are
checked against daily lows (gaps fill at the open), and costs are deducted. Limitations are stated in the
report: the universe is today's listed stocks (survivorship bias), and analyst/insider/news history is not
available point-in-time for free - those signals are measured going forward by the journal instead."""
from __future__ import annotations

import logging
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .report import CSS, e
from .signals.technical import compute_panel_features, eligibility, scores_at
from .sources import get_sources
from .util import pct

log = logging.getLogger("radar.backtest")


def _sample_universe(universe: list[dict], max_n: int) -> list[str]:
    tickers = [u["ticker"] for u in universe]
    if not max_n or len(tickers) <= max_n:
        return tickers
    step = len(tickers) / max_n  # spread across the list (SEC orders roughly by size)
    return [tickers[int(i * step)] for i in range(max_n)]


def _extra_tickers(path: str, root: Path) -> list[str]:
    if not path:
        return []
    p = Path(path) if Path(path).is_absolute() else root / path
    if not p.exists():
        return []
    df = pd.read_csv(p)
    col = df.columns[0]
    return [str(x).strip().upper() for x in df[col].dropna()]


def simulate(panel: dict[str, pd.DataFrame], filters: dict, weights: dict, bt: dict) -> dict:
    O, H, L, C = panel["Open"], panel["High"], panel["Low"], panel["Close"]
    f = compute_panel_features(panel)
    elig_all = eligibility(f, filters)
    R, hold, top_n = int(bt["rebalance_days"]), int(bt["hold_days"]), int(bt["top_n"])
    W, thr = int(bt["explosive_window_days"]), float(bt["explosive_threshold"])
    cost, use_stops, k = float(bt["cost_pct"]) / 100, bool(bt["use_stops"]), float(bt.get("atr_stop_multiple", 2.5))
    entry_px = O.shift(-1)
    fwd_ret = C.shift(-hold) / entry_px - 1
    fwd_max = H.shift(-1).rolling(W, min_periods=1).max().shift(-(W - 1)) / entry_px - 1
    dates = C.index
    start = 260
    trades, periods, ics = [], [], []
    dec_sum, dec_n = np.zeros(10), np.zeros(10)
    has_spy = "SPY" in C.columns
    for i in range(start, len(dates) - max(hold, W) - 2, R):
        t = dates[i]
        sc = scores_at(f, t, elig_all.loc[t], weights)
        if len(sc) < max(30, top_n * 3):
            continue
        sc = sc.sort_values("tech_score", ascending=False)
        fr = fwd_ret.loc[t, sc.index].dropna()
        if len(fr) > 30:
            s = sc["tech_score"].reindex(fr.index)
            ic = s.corr(fr, method="spearman")
            if not np.isnan(ic):
                ics.append(ic)
            dec = pd.qcut(s.rank(method="first"), 10, labels=False)
            for d_, v in fr.groupby(dec).mean().items():
                dec_sum[int(d_)] += v
                dec_n[int(d_)] += 1
        rets = []
        for tk in sc.index[:top_n]:
            entry = O[tk].iloc[i + 1]
            if not entry or np.isnan(entry):
                continue
            atr = f["atr14"][tk].iloc[i]
            stop = entry - k * atr if use_stops and atr and not np.isnan(atr) else -math.inf
            exit_px, reason = None, "time"
            for j in range(i + 1, i + 1 + hold):
                o, lo = O[tk].iloc[j], L[tk].iloc[j]
                if np.isnan(o) or np.isnan(lo):
                    continue
                if o <= stop:
                    exit_px, reason = o, "stop"
                    break
                if lo <= stop:
                    exit_px, reason = stop, "stop"
                    break
            if exit_px is None:
                exit_px = C[tk].iloc[i + hold]
                if np.isnan(exit_px):
                    exit_px = C[tk].iloc[i + 1:i + hold + 1].dropna().iloc[-1] if C[tk].iloc[i + 1:i + hold + 1].notna().any() else entry
            r = float(exit_px / entry - 1 - cost)
            mg = float(H[tk].iloc[i + 1:i + 1 + W].max() / entry - 1)
            rets.append(r)
            trades.append({"date": t.date().isoformat(), "ticker": tk, "score": float(sc.loc[tk, "tech_score"]),
                           "entry": float(entry), "exit": float(exit_px), "ret": r, "reason": reason, "max_gain": mg})
        if not rets:
            continue
        spy_r = float(C["SPY"].iloc[i + hold] / O["SPY"].iloc[i + 1] - 1) if has_spy else 0.0
        el = elig_all.loc[t]
        names = [x for x in el[el].index if x != "SPY"]
        periods.append({"date": t, "ret": float(np.mean(rets)), "spy": spy_r,
                        "universe": float(fwd_ret.loc[t, names].mean()),
                        "base_explosive": float((fwd_max.loc[t, names] >= thr).mean())})
    return {"trades": pd.DataFrame(trades), "periods": pd.DataFrame(periods), "ics": ics,
            "deciles": [float(dec_sum[d] / dec_n[d]) if dec_n[d] else None for d in range(10)], "R": R, "thr": thr}


def metrics(sim: dict) -> dict:
    P, T = sim["periods"], sim["trades"]
    if P.empty:
        return {}
    per_year = 252 / sim["R"]
    eq = (1 + P["ret"]).cumprod()
    spy_eq = (1 + P["spy"]).cumprod()
    years = len(P) / per_year
    dd = (eq / eq.cummax() - 1).min()
    spy_dd = (spy_eq / spy_eq.cummax() - 1).min()
    sharpe = P["ret"].mean() / P["ret"].std() * math.sqrt(per_year) if P["ret"].std() > 0 else None
    T = T.merge(P[["date", "spy"]].assign(date=P["date"].dt.date.astype(str)), on="date", how="left")
    ics = np.array(sim["ics"])
    return {
        "periods": len(P), "years": years, "trades": len(T),
        "cagr": float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else None,
        "spy_cagr": float(spy_eq.iloc[-1] ** (1 / years) - 1) if years > 0 else None,
        "total": float(eq.iloc[-1] - 1), "spy_total": float(spy_eq.iloc[-1] - 1),
        "max_dd": float(dd), "spy_max_dd": float(spy_dd), "sharpe": sharpe,
        "avg_trade": float(T["ret"].mean()), "median_trade": float(T["ret"].median()),
        "best": float(T["ret"].max()), "worst": float(T["ret"].min()),
        "beat_spy": float((T["ret"] > T["spy"]).mean()), "win_rate": float((T["ret"] > 0).mean()),
        "stop_rate": float((T["reason"] == "stop").mean()),
        "explosive": float((T["max_gain"] >= sim["thr"]).mean()), "base_explosive": float(P["base_explosive"].mean()),
        "universe_avg": float(P["universe"].mean()), "period_avg": float(P["ret"].mean()),
        "ic_mean": float(ics.mean()) if len(ics) else None,
        "ic_t": float(ics.mean() / ics.std() * math.sqrt(len(ics))) if len(ics) > 2 and ics.std() > 0 else None,
        "eq": eq.tolist(), "spy_eq": spy_eq.tolist(), "dates": [d.strftime("%m/%Y") for d in P["date"]],
        "deciles": sim["deciles"],
    }


def _line_chart(a: list[float], b: list[float], labels: list[str]) -> str:
    w, h, pad = 760, 260, 30
    allv = a + b
    lo, hi = min(allv), max(allv)
    span = (hi - lo) or 1
    def pts(v):
        n = max(1, len(v) - 1)
        return " ".join(f"{pad + i * (w - 2 * pad) / n:.1f},{h - pad - (x - lo) / span * (h - 2 * pad):.1f}" for i, x in enumerate(v))
    ticks = "".join(f'<text x="{pad + i * (w - 2 * pad) / max(1, len(labels) - 1):.0f}" y="{h - 8}" font-size="20" '
                    f'fill="var(--muted)" text-anchor="middle">{labels[i]}</text>'
                    for i in range(0, len(labels), max(1, len(labels) // 6)))
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="עקומת הון">'
            f'<polyline fill="none" stroke="var(--muted)" stroke-width="2" stroke-dasharray="5 4" points="{pts(b)}"/>'
            f'<polyline fill="none" stroke="var(--accent)" stroke-width="2.5" points="{pts(a)}"/>{ticks}</svg>'
            '<div class="sub">כחול – האסטרטגיה · מקווקו – S&P 500</div>')


def _decile_chart(dec: list) -> str:
    vals = [v if v is not None else 0 for v in dec]
    w, h, pad = 760, 200, 24
    m = max(abs(v) for v in vals) or 1
    bw = (w - 2 * pad) / 10
    zero = h / 2
    bars = []
    for i, v in enumerate(vals):
        bh = abs(v) / m * (h / 2 - pad)
        y = zero - bh if v >= 0 else zero
        color = "var(--pos)" if v >= 0 else "var(--neg)"
        x = w - pad - (i + 1) * bw + 4  # RTL: lowest decile on the right
        bars.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{bw - 8:.0f}" height="{max(bh, 1):.0f}" fill="{color}" rx="3"/>'
                    f'<text x="{x + (bw - 8) / 2:.0f}" y="{h - 4}" font-size="20" fill="var(--muted)" text-anchor="middle">{i + 1}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="תשואה לפי עשירון ציון">'
            f'<line x1="{pad}" x2="{w - pad}" y1="{zero}" y2="{zero}" stroke="var(--line)"/>{"".join(bars)}</svg>'
            '<div class="sub">תשואה ממוצעת לתקופת ההחזקה לפי עשירון הציון (1 = הציון הנמוך, 10 = הגבוה). '
            'אם הציון עובד, העמודות אמורות לגדול ככל שמתקדמים מ־1 ל־10.</div>')


def write_report(m: dict, bt: dict, path: Path, demo: bool, n_tickers: int) -> Path:
    if not m:
        path.write_text("<p>אין מספיק נתונים לבדיקה לאחור.</p>", encoding="utf-8")
        return path
    b = lambda v: f"<bdi>{v}</bdi>"  # noqa: E731  keep signed numbers readable in RTL
    ic_txt = f"{m['ic_mean']:+.3f}" if m.get("ic_mean") is not None else ""
    verdict = []
    if m["ic_mean"] is not None:
        good = m["ic_mean"] > 0.02 and (m["ic_t"] or 0) > 2
        verdict.append("לציון יש כוח ניבוי מובהק סטטיסטית בתקופה שנבדקה" if good else
                       "כוח הניבוי של הציון חלש או לא מובהק בתקופה שנבדקה – אל תסמוך עליו לבד")
    if m.get("cagr") is not None and m.get("spy_cagr") is not None:
        verdict.append("האסטרטגיה הניבה יותר מ־S&P 500 בתקופה שנבדקה" if m["cagr"] > m["spy_cagr"] else
                       "האסטרטגיה הניבה פחות מ־S&P 500 בתקופה שנבדקה – אות טוב לבדו לא מבטיח תיק טוב")
    verdict.append(f"המניות שנבחרו עלו 30% ומעלה בשלב כלשהו ב־{pct(m['explosive'])} מהמקרים, לעומת "
                   f"{pct(m['base_explosive'])} בממוצע לכל המניות")
    items = [
        f"תשואה שנתית ממוצעת: {b(pct(m['cagr'], 1, True))} · S&P 500: {b(pct(m['spy_cagr'], 1, True))}",
        f"ירידה מקסימלית מהשיא: {b(pct(m['max_dd'], 1))} · S&P 500: {b(pct(m['spy_max_dd'], 1))}",
        f"עסקה ממוצעת: {b(pct(m['avg_trade'], 1, True))} · עסקה חציונית: {b(pct(m['median_trade'], 1, True))}",
        f"העסקה הטובה ביותר: {b(pct(m['best'], 0, True))} · הגרועה ביותר: {b(pct(m['worst'], 0, True))}",
        f"עסקאות שהיכו את S&P 500: {pct(m['beat_spy'])} · עסקאות ברווח: {pct(m['win_rate'])}",
        f"עסקאות שנסגרו בסטופ: {pct(m['stop_rate'])}",
        f"מדד שארפ: {m['sharpe']:.2f}" if m.get("sharpe") is not None else "",
        f"מתאם ממוצע בין הציון לתשואה (IC): {b(ic_txt)} · מובהקות t={m['ic_t']:.1f}"
        if m.get("ic_t") is not None else "",
    ]
    demo_badge = '<span class="badge">נתוני דמו</span>' if demo else ""
    body = f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>בדיקה לאחור</title><style>{CSS}</style></head>
<body><div class="wrap"><h1>בדיקה לאחור{demo_badge}</h1>
<div class="sub">{date.today():%d/%m/%Y} · {n_tickers} מניות · {m['years']:.1f} שנים · {m['trades']} עסקאות ·
החלפה כל {bt['rebalance_days']} ימי מסחר · {bt['top_n']} מניות בכל פעם · עלות {bt['cost_pct']}% לעסקה</div>
<h2>השורה התחתונה</h2><div class="card"><ul>{''.join(f'<li>{e(v)}</li>' for v in verdict)}</ul></div>
<h2>עקומת הון</h2><div class="card">{_line_chart(m['eq'], m['spy_eq'], m['dates'])}</div>
<h2>נתונים</h2><div class="card"><ul>{''.join(f'<li>{v}</li>' for v in items if v)}</ul></div>
<h2>האם ציון גבוה יותר = תשואה גבוהה יותר?</h2><div class="card">{_decile_chart(m['deciles'])}</div>
<h2>מגבלות שחשוב לדעת</h2><div class="note"><ul>
<li>הבדיקה כוללת רק מניות שנסחרות היום. מניות שנמחקו (לרוב כאלה שקרסו) חסרות, ולכן התוצאות אופטימיות מהמציאות.</li>
<li>נבדקים רק אותות המחיר והמחזור. היסטוריה של אנליסטים, חדשות ואופציות לא זמינה בחינם "כפי שהייתה באותו יום" – את אלה
המערכת מודדת קדימה ביומן, ותראה את התוצאות בדוח היומי תחת "מה עובד?".</li>
<li>ביצועי עבר אינם מבטיחים ביצועים עתידיים. ככל שמנסים יותר הגדרות על אותם נתונים, כך גדל הסיכון ל"התאמת יתר".</li>
</ul></div><footer>כניסה במחיר הפתיחה של היום שאחרי האות · סטופ = {bt.get('atr_stop_multiple', 2.5)} × ATR · יציאה בסטופ או בסוף התקופה</footer>
</div></body></html>"""
    path.write_text(body, encoding="utf-8")
    return path


def run_backtest(settings, demo: bool = False) -> dict:
    bt = dict(settings.get("backtest"))
    bt["atr_stop_multiple"] = settings.get("risk.atr_stop_multiple")
    src = get_sources(settings, demo)
    tickers = _sample_universe(src.universe(), bt.get("max_tickers") or 0)
    tickers += _extra_tickers(settings.get("universe.extra_tickers_file"), settings.root)
    tickers = sorted(set(tickers) | {"SPY"})
    log.info("בדיקה לאחור על %d מניות, %d שנים...", len(tickers) - 1, bt["years"])
    panel = src.prices(tickers, f"{int(bt['years']) + 1}y")
    sim = simulate(panel, settings.get("filters"), settings.get("weights"), bt)
    m = metrics(sim)
    out_dir = settings.reports_dir
    stamp = f"{date.today():%Y-%m-%d}" + ("_demo" if demo else "")
    html_path = write_report(m, bt, out_dir / f"backtest_{stamp}.html", demo, len(tickers) - 1)
    if not sim["trades"].empty:
        sim["trades"].to_csv(out_dir / f"backtest_trades_{stamp}.csv", index=False, encoding="utf-8-sig")
    return {"metrics": m, "html": html_path, "n_tickers": len(tickers) - 1}
