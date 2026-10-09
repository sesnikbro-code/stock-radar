"""Takeover check and corporate events from SEC filings.

A stock that agreed to be acquired trades near the offer price: the most it can still gain is the small gap to the
deal price, and if the deal falls apart it drops back toward where it was before the announcement. To a momentum
scanner such a stock looks perfect (a jump on huge volume to a new high, short sellers trapped), so it must be
filtered out explicitly.

Evidence, strongest first:
  - merger / tender-offer filings in the company's SEC filing list (425, SC TO-T, SC 14D9, merger proxies), together
    with a price jump around the first of them (the target jumps; an acquirer usually does not)
  - a headline saying the company is being acquired
  - the price barely moving after a big one-day jump (typical of a stock pinned to a cash offer)

The same filing list (already downloaded for the insider and earnings checks, so no extra requests) also shows
offerings, late financial reports, bankruptcy and restatements. Those add risk points.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..util import SignalResult, pct

# merger, tender offer and going-private filings (normalized: upper case, no spaces/dashes, 'SCHEDULE' -> 'SC')
DEAL_FORMS = {"425", "SCTOT", "SCTOC", "SC14D9", "SC14D9C", "SC13E3", "PREM14A", "DEFM14A", "PREM14C", "DEFM14C"}
# extra proxy material: a merger only when filed next to an 8-K 'material agreement' (cash mergers)
SOFT_DEAL_FORMS = {"DEFA14A", "DFAN14A"}
OFFERING_FORMS = {"424B1", "424B3", "424B4", "424B5", "424B7", "S1", "F1"}
LATE_FORMS = {"NT10K", "NT10Q", "NT20F"}
DELIST_FORMS = {"25", "25NSE", "1512B", "1512G"}
COMPLETION_ITEMS = {"2.01", "5.01"}

MIN_JUMP = 0.10        # cumulative rise around the first deal filing that marks the company as the target
MIN_DAY_JUMP = 0.08    # ... or a single day this strong
DEAL_WINDOW_DAYS = 365
NEWS_DAYS = 45


def norm_form(form) -> tuple[str, bool]:
    f = str(form or "").upper().strip()
    amend = f.endswith("/A")
    if amend:
        f = f[:-2]
    return f.replace("SCHEDULE", "SC").replace(" ", "").replace("-", ""), amend


def parse_filings(recent: dict | None) -> list[dict]:
    """The 'filings.recent' block of SEC's submissions JSON -> [{date, form, f, items}] sorted by date."""
    if not isinstance(recent, dict):
        return []
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    items = recent.get("items") or []
    out = []
    for i, form in enumerate(forms):
        if i >= len(dates):
            break
        try:
            d = date.fromisoformat(str(dates[i])[:10])
        except ValueError:
            continue
        f, amend = norm_form(form)
        raw_items = items[i] if i < len(items) else ""
        its = {x.strip() for x in str(raw_items or "").split(",") if x.strip()}
        out.append({"date": d, "form": str(form), "f": f, "amend": amend, "items": its})
    out.sort(key=lambda x: x["date"])
    return out


def deal_cluster(filings: list[dict], asof: date) -> list[dict]:
    """Merger filings of the most recent deal (a gap of 120+ days between filings starts a new story)."""
    since = asof - timedelta(days=DEAL_WINDOW_DAYS)
    window = [x for x in filings if since <= x["date"] <= asof]
    agreements = [x["date"] for x in window if x["f"] == "8K" and "1.01" in x["items"]]
    deal = [x for x in window if x["f"] in DEAL_FORMS
            or (x["f"] in SOFT_DEAL_FORMS and any(abs((x["date"] - a).days) <= 5 for a in agreements))]
    cluster: list[dict] = []
    for x in sorted(deal, key=lambda x: x["date"]):
        if cluster and (x["date"] - cluster[-1]["date"]).days > 120:
            cluster = []
        cluster.append(x)
    return cluster


def price_reaction(closes: pd.Series | None, event: date) -> dict:
    """How the price moved around an event: rise from the close a week before to the highest close up to 2 days
    after, and the biggest single day in that window."""
    if closes is None:
        return {}
    s = pd.Series(closes).dropna()
    if len(s) < 30:
        return {}
    idx = pd.DatetimeIndex(s.index)
    ev = pd.Timestamp(event)
    before = s[idx < ev - pd.Timedelta(days=6)]
    in_win = (idx >= ev - pd.Timedelta(days=6)) & (idx <= ev + pd.Timedelta(days=2))
    win = s[in_win]
    if before.empty or win.empty:
        return {}
    base = float(before.iloc[-1])
    if base <= 0:
        return {}
    rets = s.pct_change()[in_win].dropna()
    return {"base": base, "peak": float(win.max()), "jump": float(win.max()) / base - 1,
            "day_jump": float(rets.max()) if len(rets) else 0.0}


def pinned_after_jump(closes: pd.Series | None, lookback: int = 60, min_jump: float = 0.12,
                      max_ratio: float = 0.5, min_days: int = 4, volumes: pd.Series | None = None) -> dict | None:
    """A big one-day jump in the last `lookback` days after which the stock hardly moves (daily swings at most
    half of what they were before, and within 6% of the jump-day close): the classic look of a cash offer."""
    if closes is None:
        return None
    s = pd.Series(closes).dropna()
    if len(s) < lookback + 30:
        return None
    r = s.pct_change()
    recent = r.iloc[-lookback:]
    if recent.dropna().empty:
        return None
    k = recent.idxmax()
    jump = float(recent.max())
    if not np.isfinite(jump) or jump < min_jump:
        return None
    pos = s.index.get_loc(k)
    after = r.iloc[pos + 1:].dropna()
    pre = r.iloc[max(1, pos - 60):pos].dropna()
    if len(after) < min_days or len(pre) < 20:
        return None
    if volumes is not None:  # an offer comes with huge volume on the jump day; a quiet jump is something else
        v = pd.Series(volumes).reindex(s.index)
        base_v = float(v.iloc[max(0, pos - 60):pos].median()) if pos > 0 else float("nan")
        day_v = float(v.iloc[pos]) if pd.notna(v.iloc[pos]) else float("nan")
        if np.isfinite(base_v) and base_v > 0 and np.isfinite(day_v) and day_v < 2.5 * base_v:
            return None
    pre_vol, post_vol = float(pre.std()), float(after.std())
    drift = float(s.iloc[-1] / s.iloc[pos] - 1)
    if pre_vol > 0 and post_vol <= max_ratio * pre_vol and abs(drift) <= 0.06:
        return {"date": pd.Timestamp(k).date(), "jump": jump, "ratio": post_vol / pre_vol, "drift": drift,
                "base": float(s.iloc[pos - 1])}
    return None


_SUFFIX = re.compile(r"[,\s]+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|n\.?v|"
                     r"s\.?a|ag|se|lp|llc|the)\.?$", re.I)
_GENERIC = {"global", "american", "international", "united", "first", "general", "national", "new", "the"}


def _name_core(name: str) -> str | None:
    n = str(name or "").strip()
    for _ in range(3):
        n2 = _SUFFIX.sub("", n).strip(" ,.")
        if n2 == n:
            break
        n = n2
    if len(n) < 3 or n.lower() in _GENERIC:
        return None
    return n


def _target_patterns(ticker: str, name: str) -> list[re.Pattern]:
    alts = []
    if ticker and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        alts.append(r"(?-i:" + re.escape(ticker) + r")")
    core = _name_core(name)
    if core:  # case-sensitive: "Target" the company, not "target" the word
        alts.append(r"(?-i:" + re.escape(core) + r")")
    if not alts:
        return []
    n = r"(?:" + "|".join(alts) + r")"
    not_shares = r"(?!\s+(?:stock|shares?|stake|position))"
    pats = [
        # "C.H. Robinson to acquire RXO", "agrees to buy RXO for $4.8 billion"
        rf"\b(?:to|will|set to|agrees? to|agreed to|plans? to|moves? to)\s+(?:acquire|take over)\s+(?:[\w.&'-]+\s+){{0,3}}?{n}\b{not_shares}",
        rf"\b(?:agrees? to|agreed to|deal to|offer to|offers to|bid to|bids to)\s+(?:buy|purchase)\s+(?:[\w.&'-]+\s+){{0,3}}?{n}\b{not_shares}",
        # "RXO to be acquired by ...", "RXO agrees to be bought"
        rf"\b{n}\b[^.?!]{{0,40}}?\b(?:to be|being|will be|agrees? to be|agreed to be|set to be)\s+(?:acquired|bought|taken private|sold)\b",
        # "acquisition of RXO", "takeover bid for RXO"
        rf"\b(?:acquisition of|takeover of|buyout of|purchase of|deal for|bid for|offer for|tender offer for)\s+{n}\b{not_shares}",
        rf"\bacquir(?:es|ing)\s+{n}\b{not_shares}",
        # "RXO accepts $30 offer", "RXO agrees to sale"
        rf"\b{n}\b[^.?!]{{0,40}}?\b(?:accepts|backs|recommends|agrees to|agreed to)\s+(?:[\w$.,]+\s+){{0,3}}?(?:offer|bid|buyout|takeover|sale)\b",
        rf"\b{n}\b[^.?!]{{0,60}}?\b(?:take-private|go-private|taken private)\b",
    ]
    return [re.compile(p, re.I) for p in pats]


def takeover_headline(news: list[dict] | None, ticker: str, name: str, asof: date) -> dict | None:
    """The newest headline (last 45 days) saying the company itself is being acquired."""
    pats = _target_patterns(ticker, name)
    if not pats or not news:
        return None
    since = asof - timedelta(days=NEWS_DAYS)
    hits = []
    for it in news:
        title = str(it.get("title") or "")
        pub = it.get("published")
        d = pub.date() if hasattr(pub, "date") else None
        if d is not None and d < since:
            continue
        if any(p.search(title) for p in pats):
            hits.append((d or asof, title))
    if not hits:
        return None
    d, title = max(hits, key=lambda x: x[0])
    return {"date": d, "title": title}


def _forms_text(cluster: list[dict]) -> str:
    seen = []
    for x in cluster:
        f = x["form"].replace("SCHEDULE ", "SC ")
        if f not in seen:
            seen.append(f)
    return ", ".join(seen[:3])


def deal_check(filings: list[dict], closes: pd.Series | None, news: list[dict] | None, ticker: str, name: str,
               asof: date, volumes: pd.Series | None = None) -> dict:
    """Is this stock the target of a pending acquisition?
    status: target (exclude) | completed (exclude) | suspect | involved | terminated | None"""
    out = {"status": None, "exclude": False, "text": "", "short": "", "date": None, "forms": "", "jump": None,
           "base": None}
    cluster = deal_cluster(filings or [], asof)
    headline = takeover_headline(news, ticker, name, asof)
    pin = pinned_after_jump(closes, volumes=volumes)
    if cluster:
        first, last = cluster[0]["date"], cluster[-1]["date"]
        react = price_reaction(closes, first)
        jumped = bool(react) and (react["jump"] >= MIN_JUMP or react["day_jump"] >= MIN_DAY_JUMP)
        after = [x for x in filings if x["date"] >= first]
        completed = any((x["f"] == "8K" and x["items"] & COMPLETION_ITEMS) or x["f"] in DELIST_FORMS for x in after)
        terminated = [x for x in after if x["f"] == "8K" and "1.02" in x["items"] and not x["items"] & COMPLETION_ITEMS
                      and x["date"] > last]
        out.update(date=first, forms=_forms_text(cluster), jump=react.get("jump"), base=react.get("base"))
        if completed and (jumped or headline or pin):
            out.update(status="completed", exclude=True, short="העסקה נסגרת",
                       text=f"עסקת רכישה בשלבי סגירה: יש דיווח ל־SEC על השלמת הרכישה או מחיקה מהמסחר "
                            f"(דיווחי מיזוג מאז {first:%d/%m}). אין כאן מה לקנות.")
            return out
        if terminated and not completed:
            out.update(status="terminated", short="עסקה שאולי בוטלה",
                       text=f"ייתכן שעסקת המיזוג בוטלה (דיווח 8-K על סיום הסכם ב־{terminated[-1]['date']:%d/%m}). "
                            "לבדוק בחדשות לפני כל החלטה.")
            return out
        if jumped or headline or pin:
            base = react.get("base") or (pin or {}).get("base")
            back = f", ואם העסקה תתבטל המחיר עלול לחזור לאזור {base:.2f}$" if base else ""
            rise = f" והמניה עלתה {pct(react['jump'])} סביב ההודעה" if react and react.get("jump", 0) > 0.02 else ""
            out.update(status="target", exclude=True, short="בתהליך רכישה",
                       text=f"בתהליך רכישה: מאז {first:%d/%m} יש דיווחי מיזוג ל־SEC ({out['forms']}){rise}. "
                            f"הרווח האפשרי מוגבל למחיר העסקה{back}.")
            return out
        out.update(status="involved", short="מעורבת בעסקת מיזוג",
                   text=f"החברה מעורבת בעסקת מיזוג (דיווחים ל־SEC מאז {first:%d/%m}: {out['forms']}), "
                        "כנראה כצד הרוכש. לבדוק את תנאי העסקה לפני כל החלטה.")
        return out
    if headline:
        # a headline alone can be talk (an activist, a rumor); with the price jump of an offer it is a deal
        react = price_reaction(closes, headline["date"])
        jumped = bool(react) and (react["jump"] >= MIN_JUMP or react["day_jump"] >= MIN_DAY_JUMP)
        confirmed = bool(pin) or jumped
        quote = f"כותרת מ־{headline['date']:%d/%m} מדווחת על רכישת החברה: \"{headline['title'][:110]}\". "
        if confirmed:
            base = react.get("base") or (pin or {}).get("base")
            back = f", ואם העסקה תתבטל המחיר עלול לחזור לאזור {base:.2f}$" if base else ""
            out.update(status="target", exclude=True, short="בתהליך רכישה", date=headline["date"],
                       jump=react.get("jump") or (pin or {}).get("jump"), base=base,
                       text="בתהליך רכישה: " + quote + f"המחיר קפץ בהתאם. הרווח האפשרי מוגבל למחיר העסקה{back}.")
        else:
            out.update(status="suspect", short="חשד להצעת רכישה", date=headline["date"],
                       text=quote + "אם זו עסקה חתומה, הרווח מוגבל למחיר העסקה. לבדוק לפני כל החלטה.")
        return out
    if pin:
        out.update(status="suspect", short="חשד להצעת רכישה", date=pin["date"], jump=pin["jump"], base=pin["base"],
                   text=f"המחיר כמעט לא זז מאז קפיצה של {pct(pin['jump'])} ב־{pin['date']:%d/%m}. כך נראית בדרך "
                        "כלל מניה שקיבלה הצעת רכישה. לבדוק חדשות לפני כל החלטה.")
    return out


def events_signal(filings: list[dict] | None, mcap: float | None, asof: date, deal_status: str | None = None
                  ) -> SignalResult:
    """Risk points from the company's own SEC filings: offerings (dilution), late reports, bankruptcy, restatement,
    exchange notices. Score stays 0: these never add to the upside, they only warn."""
    res = SignalResult("filings")
    flags: list[str] = []
    fl = filings or []

    def recent(days: int) -> list[dict]:
        since = asof - timedelta(days=days)
        return [x for x in fl if since <= x["date"] <= asof]

    def eight_k(days: int, item: str) -> list[dict]:
        return [x for x in recent(days) if x["f"] == "8K" and item in x["items"]]

    offers = [x for x in recent(21) if x["f"] in OFFERING_FORMS] + eight_k(21, "3.02")
    if offers and (not mcap or mcap < 1e10):
        x = max(offers, key=lambda o: o["date"])
        res.risk_add += 12
        res.risk_reasons.append(f"דיווח הנפקה ל־SEC ({x['form']}, {x['date']:%d/%m}) – ייתכן דילול ולחץ מכירות")
        flags.append("offering")
    late = [x for x in recent(90) if x["f"] in LATE_FORMS]
    if late:
        x = late[-1]
        res.risk_add += 15
        res.risk_reasons.append(f"החברה הודיעה על איחור בדוח הכספי ({x['form']}, {x['date']:%d/%m}) – סימן אזהרה")
        flags.append("late")
    bk = eight_k(180, "1.03")
    if bk:
        res.risk_add += 40
        res.risk_reasons.append(f"דיווח על פשיטת רגל או כינוס נכסים (8-K, {bk[-1]['date']:%d/%m})")
        flags.append("bankruptcy")
    restate = eight_k(120, "4.02")
    if restate:
        res.risk_add += 20
        res.risk_reasons.append(f"החברה הודיעה שאין להסתמך על דוחות כספיים קודמים ({restate[-1]['date']:%d/%m})")
        flags.append("restatement")
    listing = eight_k(90, "3.01")
    if listing and deal_status not in ("target", "completed"):
        res.risk_add += 10
        res.risk_reasons.append(f"הודעה על אי עמידה בכללי הבורסה או העברת רישום (8-K, {listing[-1]['date']:%d/%m})")
        flags.append("listing")
    res.data = {"flags": flags, "n_filings": len(fl)}
    return res
