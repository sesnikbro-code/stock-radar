"""Daily scan: universe -> cheap technical screen -> deep data on the best candidates -> signals ->
upside & risk scores -> picks -> journal / paper trading / learning -> report & alerts."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timezone

import pandas as pd

from .journal import Journal
from .scoring import composite, expected_move_3m, position_plan, risk_score
from .signals import SIGNAL_LABELS
from .signals.analysts import (firm_accuracy, firm_calls_from_history, normalize_upgrades, ratings_signal,
                               revisions_signal, target_signal)
from .signals.context import (compute_beta, compute_regime, country_signal, geopolitics_signal, gov_signal,
                              macro_signal, score_country)
from .signals.flows import options_signal, short_squeeze_signal
from .signals.insider import insider_signal, yahoo_insider_rows
from .signals.news import analyze_news, catalysts_signal
from .signals.technical import compute_panel_features, eligibility, scores_at, technical_signals
from .sources import get_sources
from .sources.countries import SHELL_DOMICILES, iso3_for
from .sources.sec import flatten_filings
from .util import MarketContext, SignalResult, TickerData, num

log = logging.getLogger("radar.pipeline")

GOV_SECTORS = {"Industrials", "Technology", "Healthcare"}
MODULE_SIGNALS = {
    "insider": ["insider_buying"], "analysts": ["analyst_revisions", "analyst_ratings", "price_target"],
    "news": ["news"], "options": ["options_flow"], "gov_contracts": ["gov_contracts"],
    "geopolitics": ["geopolitics"], "macro": ["macro"], "countries": ["country"],
}


def effective_weights(settings, journal: Journal) -> tuple[dict, dict]:
    base = dict(settings.get("weights"))
    factors = journal.factors() if settings.get("learning.enabled") else {}
    for module, names in MODULE_SIGNALS.items():
        if not settings.get(f"{module}.enabled", True):
            for n in names:
                base[n] = 0.0
    return {k: v * factors.get(k, 1.0) for k, v in base.items()}, factors


def _dedupe_news(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for n in sorted(items, key=lambda x: x.get("published") or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        key = n["title"].strip().lower()[:80]
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def fetch_ticker(src, settings, t: str, meta: dict, tech: dict, panel, spy, discovery: dict) -> TickerData:
    price = float(tech["close"])
    det = src.details(t, price, settings.get("options.enabled"))
    info = det.get("info") or {}
    name = info.get("longName") or info.get("shortName") or meta.get("name") or t
    prices = pd.DataFrame({f: panel[f][t] for f in ("Open", "High", "Low", "Close", "Volume")}).dropna(how="all")
    td = TickerData(ticker=t, name=name, cik=meta.get("cik"), price=price, tech=tech, prices=prices,
                    info=info, details=det)
    if settings.get("insider.enabled"):
        rows, source = [], ""
        if td.cik:
            try:
                rows = flatten_filings(src.insider(td.cik, settings.get("insider.lookback_days")))
                source = "SEC"
            except Exception as e:  # noqa: BLE001
                log.debug("SEC insider failed for %s: %s", t, e)
        if not rows and t in discovery and discovery[t].get("filings"):
            rows, source = flatten_filings(discovery[t]["filings"]), "SEC"
        if not rows and source != "SEC":
            rows = yahoo_insider_rows(det.get("insider_yahoo"))
            source = "Yahoo" if rows else ""
        td.insider_rows, td.insider_source = rows, source
    if settings.get("news.enabled"):
        news = list(det.get("news") or [])
        try:
            news += src.extra_news(t, settings.get("news.lookback_days"))
        except Exception as e:  # noqa: BLE001
            log.debug("extra news failed %s: %s", t, e)
        news = _dedupe_news(news)
        classified = src.classify_news(t, name, news) if news else None
        td.news = classified or news
    mcap = num(info.get("marketCap"))
    if (settings.get("gov_contracts.enabled") and mcap and mcap <= settings.get("gov_contracts.max_market_cap")
            and (info.get("sector") in GOV_SECTORS or "aerospace" in str(info.get("industry", "")).lower())):
        try:
            td.gov = src.gov_contracts(name, settings.get("gov_contracts.lookback_days"))
        except Exception as e:  # noqa: BLE001
            log.debug("gov contracts failed %s: %s", t, e)
    if spy is not None and "Close" in prices:
        td.beta = compute_beta(prices["Close"].dropna(), spy)
    return td


def build_signals(td: TickerData, ctx: MarketContext, settings) -> dict[str, SignalResult]:
    today, det, info = ctx.asof, td.details, td.info
    mcap = num(info.get("marketCap"))
    sigs = technical_signals(td.tech)
    sigs["insider_buying"] = insider_signal(td.insider_rows, mcap, settings.get("insider.lookback_days"), today,
                                            td.insider_source)
    ud = normalize_upgrades(det.get("upgrades_downgrades"))
    sigs["analyst_revisions"] = revisions_signal(det.get("eps_trend"), det.get("eps_revisions"))
    sigs["analyst_ratings"] = ratings_signal(ud, det.get("recommendations"), ctx.firm_accuracy, today)
    sigs["price_target"] = target_signal(det.get("price_targets") or {}, info, td.price)
    sigs["short_squeeze"] = short_squeeze_signal(info, td.tech)
    sigs["options_flow"] = options_signal(det.get("options"), td.tech, td.options_history)
    now = (datetime.now(timezone.utc) if today >= date.today()
           else datetime.combine(today, time(21, 0), tzinfo=timezone.utc))
    news_sig, flags = analyze_news(td.news, settings.get("news.lookback_days"), now=now,
                                   show_titles=settings.get("news.show_original_headlines"))
    sigs["news"] = news_sig
    sigs["catalysts"] = catalysts_signal(det.get("earnings_date"), sigs["analyst_revisions"].score, flags, today,
                                         settings.get("risk.earnings_blackout_days"))
    sigs["gov_contracts"] = gov_signal(td.gov, mcap, settings.get("gov_contracts.lookback_days"))
    sigs["geopolitics"] = geopolitics_signal(ctx.themes, info.get("industry", ""), info.get("longBusinessSummary", ""))
    sigs["macro"] = macro_signal(ctx.regime, td.beta, mcap, info.get("sector", ""))
    sigs["country"] = country_signal(info.get("country"), ctx.countries)
    return sigs


def explain(sigs: dict[str, SignalResult], weights: dict) -> dict:
    """Hebrew 'why' lists: strongest positive contributions first, then warnings."""
    contrib = sorted(((weights.get(k, 0) * s.conf * s.score, k, s) for k, s in sigs.items() if s.reasons),
                     key=lambda x: x[0], reverse=True)
    pros = [r for c, _k, s in contrib if c > 0.02 for r in s.reasons][:6]
    cons = [r for c, _k, s in reversed(contrib) if c < -0.02 for r in s.reasons][:4]
    return {"pros": pros, "cons": cons}


def build_context(src, settings, panel, today, candidates_info: list[TickerData], journal: Journal) -> MarketContext:
    spy = panel["Close"]["SPY"] if "SPY" in panel["Close"].columns else None
    regime = {"score": 0.0, "label": "לא נבדק", "notes": [], "rate_trend": None, "risk_off": False}
    if settings.get("macro.enabled"):
        try:
            regime = compute_regime(src.macro_series(), spy)
        except Exception as e:  # noqa: BLE001
            log.warning("ניתוח מאקרו נכשל: %s", e)
    themes = {}
    if settings.get("geopolitics.enabled"):
        try:
            themes = src.themes()
        except Exception as e:  # noqa: BLE001
            log.warning("ניתוח מלחמות ומתיחות נכשל: %s", e)
    countries = {}
    if settings.get("countries.enabled"):
        counts: dict[str, list] = {}
        for td in candidates_info:
            c = td.info.get("country")
            iso = iso3_for(c)
            if iso and iso != "USA" and c.lower() not in SHELL_DOMICILES:
                counts.setdefault(iso, [c, 0])[1] += 1
        for iso, (cname, _n) in sorted(counts.items(), key=lambda x: -x[1][1])[: settings.get("countries.max_countries")]:
            try:
                stats = src.country_stats(iso)
                if not stats:
                    continue
                conflict = src.country_conflict(cname) if settings.get("countries.check_conflict") else None
                score, reasons = score_country(stats, conflict)
                countries[iso] = {"score": score, "reasons": reasons, "stats": stats, "name": cname}
            except Exception as e:  # noqa: BLE001
                log.debug("country stats failed %s: %s", iso, e)
    calls = []
    if settings.get("analysts.enabled") and spy is not None:
        for td in candidates_info:
            ud = normalize_upgrades(td.details.get("upgrades_downgrades"))
            calls += firm_calls_from_history(td.ticker, ud, td.prices.get("Close"), spy,
                                             settings.get("analysts.accuracy_horizon_days"))
        journal.add_firm_calls(calls)
    accuracy = firm_accuracy(journal.firm_calls())
    return MarketContext(asof=today, regime=regime, themes=themes, countries=countries, firm_accuracy=accuracy,
                         spy_close=spy, is_demo=getattr(src, "is_demo", False))


SPARK_DAYS = 130


def run_scan(settings, demo: bool = False, tickers: list[str] | None = None, progress=None,
             asof: date | None = None) -> dict:
    """Run the full daily scan. `tickers` restricts the scan to a list (single-stock analysis).
    `asof` replays a past day and is only supported by the demo source."""
    src = get_sources(settings, demo, asof)
    today = asof or date.today()
    journal = Journal(settings.data_dir / ("journal_demo.sqlite" if demo else "journal.sqlite"))
    filters, weights_cfg = settings.get("filters"), settings.get("weights")

    # 1. universe ------------------------------------------------------------
    if tickers:
        lookup = src.cik_lookup()
        universe = [lookup.get(t.upper(), {"ticker": t.upper(), "cik": None, "name": t.upper()}) for t in tickers]
    else:
        universe = src.universe()
        if settings.get("universe.max_tickers"):
            universe = universe[: settings.get("universe.max_tickers")]
    meta = {u["ticker"]: u for u in universe}
    watch = [t.upper() for t in (settings.get("universe.watchlist") or [])] if not tickers else []
    if watch:
        lookup = src.cik_lookup()
        for t in watch:
            meta.setdefault(t, lookup.get(t, {"ticker": t, "cik": None, "name": t}))
    discovery = {}
    if not tickers and settings.get("insider.enabled") and settings.get("insider.discovery_enabled"):
        discovery = src.insider_discovery(settings.get("insider.discovery_days"),
                                          settings.get("insider.discovery_max_filings"))
        strong = {t: a for t, a in discovery.items()
                  if a["n_insiders"] >= settings.get("insider.cluster_min_insiders")
                  or a["value"] >= settings.get("insider.cluster_min_value")}
        discovery = strong
        lookup = src.cik_lookup() if discovery else {}
        for t in discovery:
            meta.setdefault(t, lookup.get(t, {"ticker": t, "cik": None, "name": t}))
        if discovery:
            log.info("נמצאו %d מניות עם קניות מנהלים משמעותיות בימים האחרונים", len(discovery))
    log.info("יקום המניות: %d", len(meta))

    # 2. prices + technical screen ---------------------------------------------
    panel = src.prices(sorted(set(meta) | {"SPY"}), settings.get("scan.history_period"))
    if panel["Close"].empty:
        raise RuntimeError("לא התקבלו נתוני מחירים. בדוק חיבור לאינטרנט.")
    feats = compute_panel_features(panel)
    t_last = panel["Close"].index[-1]
    elig = eligibility(feats, filters).loc[t_last].copy()
    forced = set(watch) | (set(meta) if tickers else set())  # discovery stocks must still pass the filters
    for t in forced:
        if t in elig.index and pd.notna(feats["close"][t].loc[t_last]):
            elig[t] = True
    tech_df = scores_at(feats, t_last, elig, weights_cfg)
    if tech_df.empty:
        raise RuntimeError("אף מניה לא עברה את מסנני הנזילות והמחיר.")
    ranked = tech_df.sort_values("tech_score", ascending=False)
    top_n = settings.get("scan.top_candidates")
    cands = list(ranked.index[:top_n]) if not tickers else list(ranked.index)
    cands += [t for t in forced | set(discovery) if t in tech_df.index and t not in cands]
    log.info("%d מניות עברו את המסננים, %d נבחרו לבדיקה מעמיקה", len(tech_df), len(cands))

    # 3. deep data --------------------------------------------------------------
    spy = panel["Close"]["SPY"] if "SPY" in panel["Close"].columns else None
    tds: list[TickerData] = []
    with ThreadPoolExecutor(max_workers=max(1, int(settings.get("scan.workers")))) as ex:
        futs = {ex.submit(fetch_ticker, src, settings, t, meta.get(t, {}), tech_df.loc[t].to_dict(), panel, spy,
                          discovery): t for t in cands}
        for i, fut in enumerate(as_completed(futs), 1):
            t = futs[fut]
            try:
                tds.append(fut.result())
            except Exception as e:  # noqa: BLE001
                log.warning("נכשל איסוף נתונים עבור %s: %s", t, e)
            if progress:
                progress(i, len(futs))
            elif i % 20 == 0:
                log.info("נאספו נתונים ל־%d/%d מניות", i, len(futs))
    min_cap = filters.get("min_market_cap") or 0
    tds = [td for td in tds if tickers or td.ticker in watch or not num(td.info.get("marketCap"))
           or num(td.info.get("marketCap")) >= min_cap]

    # 4. context + signals -------------------------------------------------------
    ctx = build_context(src, settings, panel, today, tds, journal)
    weights, factors = effective_weights(settings, journal)
    spark_idx = panel["Close"].index[-SPARK_DAYS:]
    results = []
    for td in tds:
        opt = td.details.get("options")
        if opt:
            td.options_history = journal.option_history(td.ticker, today)
            journal.add_option_volume(td.ticker, today, (opt.get("call_vol") or 0) + (opt.get("put_vol") or 0))
        sigs = build_signals(td, ctx, settings)
        upside, coverage = composite(sigs, weights)
        risk, risk_reasons = risk_score(td, sigs, filters)
        atr = num(td.tech.get("atr14"))
        results.append({
            "ticker": td.ticker, "name": td.name, "price": td.price, "upside": round(upside, 1),
            "risk": round(risk, 1), "coverage": coverage, "signals": sigs, "why": explain(sigs, weights),
            "risk_reasons": risk_reasons, "plan": position_plan(td.price, atr, settings.get("risk")),
            "atr": atr, "move_3m": expected_move_3m(num(td.tech.get("atr_pct"))),
            "sector": td.info.get("sector", ""), "industry": td.info.get("industry", ""),
            "country": td.info.get("country", ""), "market_cap": num(td.info.get("marketCap")),
            "earnings_date": td.details.get("earnings_date"), "insider_source": td.insider_source,
            "closes": [None if pd.isna(v) else round(float(v), 4)
                       for v in panel["Close"][td.ticker].reindex(spark_idx).values],
            "pick": False,
        })
    results.sort(key=lambda r: r["upside"], reverse=True)

    # 5. picks -------------------------------------------------------------------
    max_picks = settings.get("scan.max_picks")
    min_up = settings.get("scan.min_upside_score")
    if ctx.regime.get("risk_off"):
        max_picks, min_up = max(1, max_picks // 2), min_up + 5
    picks = [r for r in results if r["upside"] >= min_up and r["risk"] <= settings.get("scan.max_risk_score")]
    picks = picks[:max_picks] if not tickers else picks
    for r in picks:
        r["pick"] = True

    # 6. journal, paper trading, learning ---------------------------------------
    learning, paper = {}, {"n": 0}
    if not tickers:
        journal.record_run(today, ctx.regime, results, len(picks))
        n_eval = journal.evaluate(panel, today)
        if n_eval:
            log.info("נבדקו %d המלצות עבר מול התוצאה בפועל", n_eval)
        if settings.get("paper.enabled"):
            journal.paper_open(picks, today, settings.get("paper.max_open_positions"))
            journal.paper_update(panel, today, settings.get("paper"), settings.get("risk.atr_stop_multiple"))
            paper = journal.paper_summary(spy)
        if settings.get("learning.enabled"):
            learning = journal.learn(list(SIGNAL_LABELS), settings.get("learning"))
    journal.close()
    return {"date": today, "ctx": ctx, "results": results, "picks": picks, "weights": weights, "factors": factors,
            "learning": learning, "paper": paper, "n_universe": len(meta), "n_screened": len(tech_df),
            "n_deep": len(tds), "demo": demo, "single": bool(tickers),
            "spark_dates": [d.strftime("%Y-%m-%d") for d in spark_idx]}
