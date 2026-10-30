"""Daily scan: universe -> cheap technical screen -> deep data on the best candidates -> signals ->
upside & risk scores -> picks -> journal / paper trading / learning -> report & alerts."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import review as selfreview
from . import tgwatch
from .dataguard import clean_price_glitches
from .features import (EARN, INDUSTRY, INSIDER, INSIDER_HISTORY_DAYS, IndustryPeers, live_features, market_at,
                       tech_frame)
from .journal import Journal
from .model import ProbModel, model_spec, probability_signal
from .scoring import composite, expected_move_3m, position_plan, risk_score
from .signals import SIGNAL_LABELS
from .signals.analysts import (firm_accuracy, firm_calls_from_history, normalize_upgrades, ratings_signal,
                               revisions_signal, target_signal)
from .signals.earnings import earnings_drift_signal
from .signals.context import (compute_beta, compute_regime, country_signal, geopolitics_signal, gov_signal,
                              macro_signal, score_country)
from .signals.deals import deal_check, events_signal, parse_filings
from .signals.flows import options_signal, short_squeeze_signal
from .signals.insider import insider_signal, yahoo_insider_rows
from .signals.news import analyze_news, catalysts_signal
from .signals.technical import compute_panel_features, eligibility, scores_at, technical_signals
from .sources import get_sources
from .sources.countries import SHELL_DOMICILES, iso3_for
from .sources import telegram
from .sources.sec import flatten_filings, is_fund_name
from .train import base_plan
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
        if td.cik and source == "SEC" and any(r.get("code") == "P" for r in rows):
            try:  # someone bought: look 15 months back to see whether this insider buys routinely
                td.insider_rows_long = flatten_filings(src.insider(td.cik, INSIDER_HISTORY_DAYS, 120))
            except Exception as e:  # noqa: BLE001
                log.debug("long insider history failed for %s: %s", t, e)
    if td.cik and hasattr(src, "company_sic"):
        td.sic = src.company_sic(td.cik)
    if td.cik and settings.get("deals.enabled", True):
        try:  # same SEC filing list as the insider and earnings checks (cached), used for the takeover check
            recent = src.filings(td.cik)
            td.filings = parse_filings(recent) if recent is not None else None
        except Exception as e:  # noqa: BLE001
            log.debug("SEC filings failed for %s: %s", t, e)
    if settings.get("news.enabled"):
        news = list(det.get("news") or [])
        try:
            news += src.extra_news(t, settings.get("news.lookback_days"), name=name, have=len(news))
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
    try:
        td.earnings = src.earnings_events(t, td.cik, det)
    except Exception as e:  # noqa: BLE001
        log.debug("earnings events failed %s: %s", t, e)
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


DEAL_RISK = {"suspect": 15, "terminated": 10, "involved": 5}


def corporate_events(td: TickerData, sigs: dict[str, SignalResult], today: date, enabled: bool = True) -> dict:
    """Takeover check + risk from the company's SEC filings. Adds sigs['filings'] and returns the deal verdict."""
    if not enabled:
        return {"status": None, "exclude": False, "text": "", "short": ""}
    closes = td.prices["Close"] if "Close" in td.prices else None
    vols = td.prices["Volume"] if "Volume" in td.prices else None
    deal = deal_check(td.filings or [], closes, td.news, td.ticker, td.name, today, vols)
    ev = events_signal(td.filings, num(td.info.get("marketCap")), today, deal.get("status"))
    ev.risk_add += DEAL_RISK.get(deal.get("status"), 0)
    ev.data["checked"] = td.filings is not None
    sigs["filings"] = ev
    return deal


def vol_penalty(atr_pct: float | None, settings) -> float:
    """Points off the upside score for a very volatile stock. Over 5 years the most volatile fifth of the stocks
    lost 1.55% per trade on average while the calmest fifth made 0.31%."""
    if atr_pct is None or not np.isfinite(atr_pct):
        return 0.0
    lo = float(settings.get("scoring.vol_penalty_from") or 0.045)
    hi = float(settings.get("scoring.vol_penalty_full") or 0.09)
    mx = float(settings.get("scoring.vol_penalty_max") if settings.get("scoring.vol_penalty_max") is not None else 8)
    return float(np.clip((atr_pct - lo) / max(1e-9, hi - lo), 0, 1) * mx)


def is_fund(td: TickerData) -> bool:
    """A fund / ETF / trust that got past the name filter: Yahoo's quote type, or the name."""
    qt = str(td.info.get("quoteType") or "").upper()
    return qt in ("ETF", "MUTUALFUND", "INDEX", "CRYPTOCURRENCY", "CURRENCY", "FUTURE") or is_fund_name(td.name)


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

def model_prescore(model: ProbModel, feats: dict, t_last, universe: list[str], eligible: pd.Series,
                   spy: pd.Series | None) -> pd.Series:
    """The model's score for every stock that passed the liquidity filters, from what is known without fetching
    each company's data: price and volume, the market, and the industry (insider buying and earnings reports are
    added for the finalists). Decides which stocks get the full check, and each stock's place in today's ranking."""
    tf = tech_frame(feats, t_last).reindex(universe)
    for k in INSIDER:
        tf[k] = 0.0
    for k in EARN:
        tf[k] = np.nan
    for k, v in (market_at(spy, len(spy) - 1) if spy is not None else {}).items():
        tf[k] = v
    imap = model.meta.get("industry_map") or {}
    if imap:
        ind = IndustryPeers(imap).frame(feats["mom_6_1"].loc[t_last], feats["rs_63"].loc[t_last], eligible,
                                        {t: imap.get(t) for t in universe})
        for k in INDUSTRY:
            tf[k] = ind[k].reindex(universe).to_numpy()
    X = tf.reindex(columns=model.features).to_numpy(dtype=float)
    return pd.Series(model.score_raw(X), index=universe)


def _tg_fields(found: list[dict], enabled: bool) -> dict:
    """Telegram mentions of one stock: the newest 3 and how many in the period (None when the channels were not read)."""
    return {"tg": found[:3], "tg_n": len(found) if enabled else None}


def data_quality(health: dict) -> list[str]:
    """Reasons the scan's data is too partial to act on (the cloud's backup run tries again later)."""
    n, out = health.get("n") or 0, []
    if n < 20:
        return out
    if health.get("analysts", n) < 0.3 * n and health.get("sector", n) < 0.5 * n:
        out.append("הנתונים מ־Yahoo (אנליסטים, ענף ושווי שוק) לא התקבלו")
    elif health.get("analysts", n) < 0.3 * n:
        out.append("נתוני האנליסטים לא התקבלו")
    if health.get("filings", n) < 0.3 * n:
        out.append("דיווחי SEC לא התקבלו")
    return out


def load_model(settings, demo: bool, model_path: Path | None) -> ProbModel | None:
    path = Path(model_path) if model_path else settings.data_dir / ("model_demo.json" if demo else "model.json")
    m = ProbModel.load(path) if path.exists() else None
    if m is not None and bool(m.meta.get("demo")) != bool(demo):
        return None  # never mix a demo model with real data (or the other way round)
    if m is not None and m.meta.get("spec") != model_spec(settings):
        log.info("מודל הסיכוי השמור עונה על שאלה אחרת מההגדרות הנוכחיות – לא בשימוש עד האימון הבא")
        return None
    return m


def run_scan(settings, demo: bool = False, tickers: list[str] | None = None, progress=None,
             asof: date | None = None, model_path: Path | None = None) -> dict:
    """Run the full daily scan. `tickers` restricts the scan to a list (single-stock analysis).
    `asof` replays a past day and is only supported by the demo source."""
    src = get_sources(settings, demo, asof)
    model = load_model(settings, demo, model_path)
    if model is not None:
        log.info("מודל הסיכוי נטען (אומן %s)", str(model.meta.get("trained", ""))[:10])
    today = asof or date.today()
    journal = Journal(settings.data_dir / ("journal_demo.sqlite" if demo else "journal.sqlite"))
    filters, weights_cfg = settings.get("filters"), settings.get("weights")
    rstate = selfreview.load_state(journal)          # what the self-review concluded after the last scan
    mid = selfreview.model_id(model) if model is not None and model.quality > 0 else None

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
    panel, price_fixes = clean_price_glitches(panel)   # one wrong price must not hit a stop or make a breakout
    feats = compute_panel_features(panel)
    t_last = panel["Close"].index[-1]
    elig = eligibility(feats, filters).loc[t_last].copy()
    forced = set(watch) | (set(meta) if tickers else set())  # discovery stocks must still pass the filters
    # saved / watchlist stocks are analysed every day even when they fail the liquidity and price filters, but such a
    # stock is never picked: the picks stay the stocks that passed the same rules as everything else
    watch_only = {t for t in watch if t in elig.index and not bool(elig[t])} if not tickers else set()
    for t in forced:
        if t in elig.index and pd.notna(feats["close"][t].loc[t_last]):
            elig[t] = True
    tech_df = scores_at(feats, t_last, elig, weights_cfg)
    if tech_df.empty:
        raise RuntimeError("אף מניה לא עברה את מסנני הנזילות והמחיר.")
    ranked = tech_df.sort_values("tech_score", ascending=False)
    top_n = settings.get("scan.top_candidates")
    spy_all = panel["Close"]["SPY"] if "SPY" in panel["Close"].columns else None
    # picks come from the model when it passed its test (and the self-review did not find it failing live)
    use_model = (model is not None and model.quality > 0 and bool(settings.get("scan.pick_by_model", True))
                 and not (rstate.get("drift") == "bad" and rstate.get("model_id") == mid))
    pre = None
    if use_model and not tickers:
        try:
            universe_ok = [t for t in tech_df.index if t != "SPY"]
            pre = model_prescore(model, feats, t_last, universe_ok, elig, spy_all).dropna()
        except Exception as e:  # noqa: BLE001
            log.warning("דירוג המודל לכל השוק נכשל, הבחירה חוזרת לציון המשולב: %s", e)
            pre = None
    if tickers:
        cands = list(ranked.index)
    elif pre is not None and len(pre):
        n_band = int(np.ceil(model.band_frac * len(pre)))
        n_model = min(150, max(int(settings.get("scan.model_candidates") or 80), n_band + 10))
        cands = list(pre.sort_values(ascending=False).index[:n_model])
        cands += [t for t in ranked.index[:int(settings.get("scan.tech_candidates") or 30)] if t not in set(cands)]
        log.info("המודל דירג %d מניות. %d המובילות שלו (מתוכן %d ברמת הבחירה) ועוד %d לפי הסינון הטכני עוברות לבדיקה "
                 "מעמיקה", len(pre), n_model, n_band, len(cands) - n_model)
    else:
        cands = list(ranked.index[:top_n])
    cands += [t for t in forced | set(discovery) if t in tech_df.index and t not in cands]
    pre_sorted = np.sort(pre.to_numpy()) if pre is not None and len(pre) else None
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
    tg_posts, tg_at, tg_reach, tg_on = [], None, {}, tgwatch.enabled(settings)
    if tg_on and hasattr(src, "telegram_posts"):
        try:   # public Telegram channels: which of these stocks the Israeli public is reading about
            tg_posts, tg_at, tg_reach = tgwatch.read(src, settings)
            log.info("נקראו %d הודעות מערוצי הטלגרם", len(tg_posts))
        except Exception as e:  # noqa: BLE001
            log.warning("קריאת ערוצי הטלגרם נכשלה: %s", e)
    weights, factors = effective_weights(settings, journal)
    plan = (model.plan if use_model and model.plan else None) or base_plan(settings)   # stop, target, holding time
    if model is None or model.quality <= 0:
        weights["model"] = 0.0  # no tested model yet: don't let its empty slot dilute the other signals
    elif rstate.get("drift") == "bad" and rstate.get("model_id") == mid:
        weights["model"] *= 0.5  # the self-review found that this model picks worse live than it rejects
        log.info("הביקורת העצמית מצאה שהמודל מצליח בפועל פחות מבבדיקה – המשקל שלו הוקטן בחצי עד האימון הבא")
    spark_idx = panel["Close"].index[-SPARK_DAYS:]
    industry = {}
    if model is not None and model.meta.get("industry_map"):
        try:  # industry momentum from the same peer list the model was trained with
            industry = IndustryPeers(model.meta["industry_map"]).frame(
                feats["mom_6_1"].loc[t_last], feats["rs_63"].loc[t_last], eligibility(feats, filters).loc[t_last],
                {td.ticker: td.sic for td in tds}).to_dict(orient="index")
        except Exception as e:  # noqa: BLE001
            log.warning("חישוב מומנטום הענפים נכשל: %s", e)
    results = []
    for td in tds:
        opt = td.details.get("options")
        if opt:
            td.options_history = journal.option_history(td.ticker, today)
            journal.add_option_volume(td.ticker, today, (opt.get("call_vol") or 0) + (opt.get("put_vol") or 0))
        sigs = build_signals(td, ctx, settings)
        deal = corporate_events(td, sigs, today, settings.get("deals.enabled", True))
        prob, hist, mf, bucket, plive, prob_model, score, pct = None, None, None, None, None, None, None, None
        try:
            long_rows = td.insider_rows_long
            mf = live_features(td.tech, long_rows if long_rows is not None else td.insider_rows, td.earnings,
                               td.prices["Close"], spy, today, industry=industry.get(td.ticker),
                               insider_since=today - timedelta(days=INSIDER_HISTORY_DAYS if long_rows is not None
                                                               else settings.get("insider.lookback_days")))
            sigs["earnings_drift"] = earnings_drift_signal(mf)
            if model is not None and model.quality > 0:  # only a model that passed its out-of-sample test
                score = model.score(mf)
                if pre_sorted is not None:   # place in today's ranking of all stocks
                    pct = float(np.searchsorted(pre_sorted, score, side="right")) / len(pre_sorted)
                bucket, hist = model.rank_bucket(pct, score)
                prob = prob_model = model.display_prob(score, hist)
                # what really happened so far to my recommendations at this level corrects the test's number
                plive = selfreview.live_bucket(rstate, mid, bucket, hist)
                if prob is not None and plive and plive.get("rate_adj") and (hist or {}).get("rate"):
                    prob = min(0.95, max(0.01, prob * plive["rate_adj"] / hist["rate"]))
                sigs["model"] = probability_signal(score, model, hist)
        except Exception as e:  # noqa: BLE001
            log.warning("חישוב מודל הסיכוי נכשל עבור %s: %s", td.ticker, e)
        upside, coverage = composite(sigs, weights)
        # The volatility penalty corrects the combined score. When the probability model picks (it passed its test
        # and already weighs volatility among its features), the penalty would only veto its picks a second time,
        # so the stock keeps its score and the warning stays.
        vol_raw = vol_penalty(num(td.tech.get("atr_pct")), settings)
        vpen = 0.0 if (use_model and pre is not None and not tickers) else vol_raw
        upside = max(0.0, upside - vpen)
        risk, risk_reasons = risk_score(td, sigs, filters)
        atr = num(td.tech.get("atr14"))
        why = explain(sigs, weights)
        if vol_raw >= 2:
            day = f"תנודתית מאוד (כ־{num(td.tech.get('atr_pct')) * 100:.1f}% ביום בממוצע): ב־5 שנים מניות כאלה הרוויחו פחות"
            why["cons"].append(f"{day}, ולכן ירדו {vpen:.0f} נקודות מהפוטנציאל" if vpen
                               else f"{day} בממוצע. המודל כבר מביא את התנודתיות בחשבון, אבל צפויות תנודות חדות "
                                    f"והסטופ רחוק – כמות קטנה")
        fund = is_fund(td)
        if fund:
            why["cons"].insert(0, "זו קרן או תעודת סל, לא חברה – לא נבחרת")
        if deal.get("text"):
            why["cons"].insert(0, deal["text"])
        flags = list(sigs["filings"].data.get("flags", [])) if "filings" in sigs else []
        exclude = deal.get("short") if deal.get("exclude") else ("פשיטת רגל" if "bankruptcy" in flags else None)
        close, sma50 = num(td.tech.get("close")), num(td.tech.get("sma50"))
        pt = td.details.get("price_targets") or {}
        results.append({
            "ticker": td.ticker, "name": td.name, "price": td.price, "upside": round(upside, 1),
            "risk": round(risk, 1), "coverage": coverage, "signals": sigs, "why": why,
            "exclude": exclude, "flags": flags, "fund": fund, "vol_penalty": round(vpen, 1),
            "deal": {k: deal.get(k) for k in ("status", "short", "text", "date", "forms", "jump", "base")}
            if deal.get("status") else None,
            "filings_checked": td.filings is not None,
            "rsi": num(td.tech.get("rsi14")), "ext50": close / sma50 - 1 if close and sma50 else None,
            "target_mean": num(pt.get("mean")) or num(td.info.get("targetMeanPrice")),
            "n_analysts": num(td.info.get("numberOfAnalystOpinions")),
            "risk_reasons": risk_reasons, "plan": position_plan(td.price, atr, settings.get("risk"), plan),
            "atr": atr, "move_3m": expected_move_3m(num(td.tech.get("atr_pct"))),
            "sector": td.info.get("sector", ""), "industry": td.info.get("industry", ""),
            "country": td.info.get("country", ""), "market_cap": num(td.info.get("marketCap")),
            "earnings_date": td.details.get("earnings_date"), "insider_source": td.insider_source,
            "prob": prob, "prob_base": model.base if model is not None and prob is not None else None,
            "prob_live": plive if prob is not None else None, "prob_model": prob_model,
            "bucket": bucket, "_feats": mf, "dollar_vol": num(td.tech.get("dollar_vol20")),
            "model_score": score, "model_pct": pct,
            "in_band": bool(pct is not None and model is not None and pct >= 1 - model.band_frac),
            "prob_hist": {k: hist.get(k) for k in ("ret", "rate", "stop", "win", "n", "band")} if hist else None,
            "exp_base": model.base_ret if model is not None and prob is not None else None,
            "prob_rule": {"mode": model.meta.get("target_mode") or "pct", "r": model.meta.get("target_r"),
                          "pct": model.meta.get("target"), "horizon": model.meta.get("horizon")}
            if model is not None and prob is not None else None,
            "closes": [None if pd.isna(v) else round(float(v), 4)
                       for v in panel["Close"][td.ticker].reindex(spark_idx).values],
            "pick": False, "why_not": None, "lesson": None,
            **_tg_fields(telegram.mentions(tg_posts, td.ticker, td.name) if tg_posts else [], bool(tg_posts)),
        })

    # 5. picks -------------------------------------------------------------------
    health = {"n": len(tds), "news": sum(1 for td in tds if td.news),
              "filings": sum(1 for td in tds if td.filings is not None),
              "insider_sec": sum(1 for td in tds if td.insider_source == "SEC"),
              "analysts": sum(1 for td in tds if td.details.get("eps_trend") is not None),
              "options": sum(1 for td in tds if td.details.get("options")),
              "sector": sum(1 for td in tds if td.info.get("sector")),
              "telegram": len(tg_posts) if tg_on else None, "price_fixes": sum(price_fixes.values()),
              "funds": sum(1 for r in results if r.get("fund"))}
    degraded = data_quality(health) if not tickers else []
    if degraded:
        log.warning("הסריקה חלקית: %s. לא נפתחות עסקאות בתיק הנייר, וריצת הגיבוי תנסה שוב.", "; ".join(degraded))
    max_picks = settings.get("scan.max_picks")
    min_up = settings.get("scan.min_upside_score")
    if ctx.regime.get("risk_off"):
        max_picks, min_up = max(1, max_picks // 2), min_up + 5
    model_mode = use_model and pre is not None and not tickers
    if model_mode:
        # the model chooses; the other signals can only veto a stock whose overall picture is clearly negative
        min_up = float(settings.get("scan.min_upside_model") or 60)
    # lessons the self-review adopted (found in live results and confirmed on history): those stocks are skipped
    lessons = selfreview.active_lessons(rstate)
    if lessons and results:
        frame = pd.DataFrame([{**(r.get("_feats") or {}), "run_date": today.isoformat(),
                               "earnings_date": r.get("earnings_date"),
                               "risk_off": int(bool(ctx.regime.get("risk_off"))), "ext50": r.get("ext50")}
                              for r in results])
        cf = selfreview.cond_frame(frame, rstate.get("thresholds") or selfreview.thresholds_for(model))
        for i, r in enumerate(results):
            hit = next((lz for lz in lessons if lz["key"] in cf and cf[lz["key"]].iloc[i] == 1), None)
            if hit:
                r["lesson"] = {"key": hit["key"], "text": hit["text"]}

    def level_ret(r):   # what the stock's level earned in the test, corrected by what really happened since
        live = r.get("prob_live") or {}
        return live.get("ret_adj", (r.get("prob_hist") or {}).get("ret") or 0)

    # why each stock was or was not picked (the self-review checks later whether each filter was right)
    check_edge = settings.get("scan.require_model_edge") and model is not None and model.quality > 0
    for r in results:
        if r.get("fund"):             # a fund or ETF, not a company
            r["why_not"] = "fund"
        elif r.get("exclude"):        # in a pending takeover or bankrupt: the upside is capped or gone
            r["why_not"] = "deal"
        elif r["ticker"] in watch_only:
            r["why_not"] = "filters"
        elif r["risk"] > settings.get("scan.max_risk_score"):
            r["why_not"] = "risk"
        elif model_mode and not r.get("in_band"):
            r["why_not"] = "band"
        elif r["upside"] < min_up:
            r["why_not"] = "upside"
        elif check_edge and r.get("prob_hist") and level_ret(r) <= 0:
            r["why_not"] = "edge"
        elif r.get("lesson"):
            r["why_not"] = "lesson:" + r["lesson"]["key"]
    order = (lambda r: -(r.get("model_score") if r.get("model_score") is not None else -9)) if model_mode \
        else (lambda r: -r["upside"])
    passing = sorted([r for r in results if r["why_not"] is None], key=order)
    per_sector = int(settings.get("scan.max_per_sector") or 0)
    picks, count = [], {}
    for r in passing:
        sec_ = r.get("sector") or ""
        if tickers:
            picks.append(r)
        elif per_sector and sec_ and count.get(sec_, 0) >= per_sector:
            r["why_not"] = "sector"
        elif len(picks) >= max_picks:
            r["why_not"] = "rank"
        else:
            picks.append(r)
            count[sec_] = count.get(sec_, 0) + 1
    for r in picks:
        r["pick"] = True
    skipped = {}
    for r in results:
        if r["why_not"]:
            k = r["why_not"].split(":")[0]
            skipped[k] = skipped.get(k, 0) + 1
    log.info("נבחרו %d מניות (%s). לא נבחרו: %s", len(picks), "לפי המודל" if model_mode else "לפי הציון המשולב",
             ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())))
    # the list: picks first in the order they were chosen, then the rest by the model's ranking (or the score)
    rest = sorted([r for r in results if not r["pick"]], key=order)
    results = picks + rest
    tg_sum, tg_found = (tgwatch.summary(settings, tg_posts, results, src, meta, tg_at, "scan", tg_reach) if tg_on
                        else (None, []))

    # 6. journal, paper trading, learning ---------------------------------------
    learning, paper, review, tg_feed = {}, {"n": 0}, None, None
    if not tickers:
        journal.record_run(today, ctx.regime, results, len(picks))
        if tg_on:
            try:   # the log of what was taken from Telegram (the app's Telegram screen)
                tg_feed = tgwatch.record(journal, tg_sum, tg_found)
            except Exception as e:  # noqa: BLE001
                log.warning("שמירת יומן הטלגרם נכשלה: %s", e)
        try:
            selfreview.record(journal, today, t_last.date(), results, ctx, model,
                              float(settings.get("train.cost_pct") or 0.2) / 100, plan)
        except Exception as e:  # noqa: BLE001 - the scan itself must not fail because of the review
            log.warning("שמירת התחזיות לביקורת העצמית נכשלה: %s", e)
        n_eval = journal.evaluate(panel, today)
        if n_eval:
            log.info("נבדקו %d המלצות עבר מול התוצאה בפועל", n_eval)
        if settings.get("paper.enabled"):
            if not degraded:   # partial data: no new positions until a full scan
                journal.paper_open(picks, today, settings.get("paper.max_open_positions"), bar_date=t_last.date(),
                                   max_new=int(settings.get("paper.max_new_per_day") or 0), plan=plan)
            journal.paper_update(panel, today, settings.get("paper"), settings.get("risk.atr_stop_multiple"),
                                 settings.get("risk.target_r") if settings.get("paper.take_profit") else None,
                                 default_plan=base_plan(settings))
            paper = journal.paper_summary(spy, settings.get("risk.target_r") if settings.get("paper.take_profit") else None,
                                          default_plan=base_plan(settings))
        try:
            review = selfreview.run(journal, panel, feats, today, settings, model)
            log.info("ביקורת עצמית: %d מניות נבדקו אחרי חודש, %d לקחים פעילים", (review.get("counts") or {}).get("graded", 0),
                     sum(1 for lz in review.get("lessons") or [] if lz["status"] in ("adopted", "adopted_live")))
        except Exception as e:  # noqa: BLE001
            log.warning("הביקורת העצמית נכשלה: %s", e)
        if settings.get("learning.enabled"):
            learning = journal.learn(list(SIGNAL_LABELS), settings.get("learning"),
                                     skip=set((review or {}).get("factors") or {}))
    journal.close()
    return {"date": today, "ctx": ctx, "results": results, "picks": picks, "weights": weights, "factors": factors,
            "health": health, "review": review, "degraded": degraded, "plan": plan,
            "telegram": tg_sum, "telegram_feed": tg_feed,
            "pick_mode": "model" if model_mode else "score",
            "band_frac": model.band_frac if model_mode else None,
            "learning": learning, "paper": paper, "n_universe": len(meta), "n_screened": len(tech_df),
            "n_deep": len(tds), "demo": demo, "single": bool(tickers),
            "model": _model_summary(model),
            "spark_dates": [d.strftime("%Y-%m-%d") for d in spark_idx],
            # S&P 500 on the same days: the saved-stocks tab compares a stock with the index since it was saved
            "spy_closes": [None if pd.isna(v) else round(float(v), 4) for v in spy.reindex(spark_idx).values]
            if spy is not None else []}


def _model_summary(model: ProbModel | None) -> dict | None:
    if model is None:
        return None
    v = model.meta.get("validation") or {}
    money = v.get("money") or {}
    return {"trained": model.meta.get("trained"), "base": model.base, "auc": v.get("auc"),
            "top_rate": v.get("top_rate"), "top_lift": v.get("top_lift"), "quality": model.quality,
            "passed": v.get("passed"), "target_mode": model.meta.get("target_mode"),
            "target": model.meta.get("target"), "target_r": model.meta.get("target_r"),
            "horizon": model.meta.get("horizon"), "stop_atr": model.meta.get("stop_atr"),
            "top_ret": (money.get("model") or {}).get("ret"), "all_ret": (money.get("all") or {}).get("ret"),
            "spy_ret": (money.get("model") or {}).get("spy"), "top_win": (money.get("model") or {}).get("win"),
            "top_stop": (money.get("model") or {}).get("stop"), "test_start": v.get("test_start"),
            "test_end": v.get("test_end"), "kind": model.kind, "sel_start": v.get("sel_start"),
            "sel_end": v.get("sel_end"), "n_candidates": len(v.get("candidates") or []),
            "band_frac": model.band_frac, "plan_key": (model.plan or {}).get("key"),
            "band_ret": (model.band_stats() or {}).get("ret"), "band_all": model.base_ret}
