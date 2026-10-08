"""News sentiment (keyword engine, or Claude when enabled) and upcoming catalysts."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..util import SignalResult, clip

# category: (weight, Hebrew label, phrases)
CATEGORIES = {
    "guidance_up": (0.7, "העלאת תחזית", ["raises guidance", "raises outlook", "raises forecast", "boosts outlook",
                                        "lifts guidance", "raised guidance", "raises full-year", "ups guidance"]),
    "earnings_beat": (0.6, "דוח מעל הציפיות", ["beats estimates", "beat estimates", "tops estimates", "beats expectations",
                                              "beat expectations", "exceeds expectations", "record revenue", "record quarter"]),
    "fda_positive": (0.8, "התקדמות רגולטורית/קלינית חיובית", ["fda approval", "fda approves", "approved by the fda",
                                                              "breakthrough therapy", "positive topline", "met primary endpoint",
                                                              "positive phase", "fast track designation"]),
    "mna": (0.7, "דיווח על עסקת רכישה/מיזוג", ["to be acquired", "buyout", "takeover", "agrees to be acquired",
                                               "explores sale", "strategic alternatives", "acquisition offer", "tender offer"]),
    "contract": (0.5, "חוזה או שיתוף פעולה חדש", ["awarded", "wins contract", "contract award", "selected by",
                                                  "signs agreement", "strategic partnership", "collaboration agreement",
                                                  "order from", "multi-year contract"]),
    "upgrade": (0.4, "שדרוג אנליסט", ["upgrade", "upgraded", "upgrades", "raises price target", "price target raised"]),
    "buyback": (0.3, "תוכנית רכישה עצמית", ["buyback", "share repurchase", "repurchase program"]),
    "positive_generic": (0.2, "חדשות חיוביות", ["surges", "soars", "jumps", "rallies", "strong demand", "expands",
                                               "launches", "record high"]),
    "guidance_down": (-0.7, "הורדת תחזית", ["cuts guidance", "lowers guidance", "cuts outlook", "lowers outlook",
                                           "slashes", "withdraws guidance", "cuts forecast"]),
    "earnings_miss": (-0.6, "דוח מתחת לציפיות", ["misses estimates", "miss estimates", "falls short", "below estimates",
                                                "misses expectations"]),
    "fda_negative": (-0.9, "כישלון רגולטורי/קליני", ["complete response letter", "crl", "failed to meet", "did not meet",
                                                    "clinical hold", "fda rejects"]),
    "dilution": (-0.7, "הנפקת מניות (דילול)", ["public offering", "registered direct", "priced offering", "at-the-market",
                                              "stock offering", "share offering", "convertible notes", "private placement"]),
    "legal": (-0.6, "חקירה או תביעה", ["lawsuit", "class action", "investigation", "subpoena", "sec probe",
                                       "fraud", "short seller", "short report"]),
    "distress": (-0.9, "מצוקה פיננסית", ["bankruptcy", "chapter 11", "going concern", "delisting", "default",
                                         "reverse split", "reverse stock split"]),
    "downgrade": (-0.4, "הורדת דירוג", ["downgrade", "downgraded", "downgrades", "cuts price target",
                                        "price target cut", "lowers price target"]),
    "management_exit": (-0.3, "עזיבת מנכ\"ל/סמנכ\"ל כספים", ["ceo resigns", "cfo resigns", "steps down", "ceo departure"]),
    "negative_generic": (-0.2, "חדשות שליליות", ["plunges", "tumbles", "slumps", "weak demand", "recall", "halts"]),
}
PDUFA = re.compile(r"\bpdufa\b", re.I)
LLM_EVENT_MAP = {"guidance": None, "earnings": None, "contract": "contract", "regulatory": None, "clinical": None,
                 "mna": "mna", "dilution": "dilution", "distress": "distress", "legal": "legal"}

_PATTERNS = {k: re.compile(r"\b(" + "|".join(re.escape(p) for p in v[2]) + r")\b", re.I) for k, v in CATEGORIES.items()}


def lexicon_score(title: str, summary: str = "") -> tuple[float, list[str]]:
    score, cats = 0.0, []
    for k, (w, _he, _p) in CATEGORIES.items():
        if _PATTERNS[k].search(title or ""):
            score += w
            cats.append(k)
        elif _PATTERNS[k].search(summary or ""):
            score += w * 0.5
            cats.append(k)
    if PDUFA.search((title or "") + " " + (summary or "")):
        cats.append("pdufa")
    return clip(score), cats


def _age_days(published, now: datetime) -> float | None:
    if not published:
        return None
    return max(0.0, (now - published).total_seconds() / 86400)


def analyze_news(news: list[dict], lookback_days: int, now: datetime | None = None,
                 show_titles: bool = True) -> tuple[SignalResult, set[str]]:
    now = now or datetime.now(timezone.utc)
    res = SignalResult("news")
    flags: set[str] = set()
    scored = []
    for n in news:
        age = _age_days(n.get("published"), now)
        if age is not None and age > 30:
            continue
        if "llm_sentiment" in n:
            s = n["llm_sentiment"] * (0.5 + 0.5 * n.get("llm_impact", 0.5))
            ev = LLM_EVENT_MAP.get(n.get("llm_event"))
            cats = [ev] if ev else []
            if n.get("llm_event") in ("regulatory", "clinical"):
                cats.append("fda_positive" if s > 0 else "fda_negative")
            label = n.get("he") or ""
        else:
            s, cats = lexicon_score(n.get("title", ""), n.get("summary", ""))
            label = CATEGORIES[cats[0]][1] if cats and cats[0] in CATEGORIES else ""
        flags.update(cats)  # 30-day window for catalyst flags
        if age is not None and age > lookback_days:
            continue
        w = 0.5 ** ((age if age is not None else lookback_days / 2) / 3)
        scored.append({**n, "s": s, "w": w, "label": label, "cats": cats})
    if not scored:
        return res, flags
    tw = sum(x["w"] for x in scored)
    avg = sum(x["w"] * x["s"] for x in scored) / tw if tw else 0.0
    res.score = clip(avg * 1.3) * (0.6 + 0.4 * min(1.0, len(scored) / 4))
    res.conf = 0.8
    top = sorted(scored, key=lambda x: abs(x["w"] * x["s"]), reverse=True)[:2]
    for x in top:
        if abs(x["s"]) < 0.15 or not x["label"]:
            continue
        side = "חדשות חיוביות" if x["s"] > 0 else "חדשות שליליות"
        src = f" ({x['source']})" if x.get("source") else ""
        res.reasons.append(f"{side}: {x['label']}{src}")
    res.data = {"n": len(scored), "items": [{"title": x["title"], "url": x.get("url", ""), "s": x["s"],
                                             "label": x["label"]} for x in top] if show_titles else []}
    if "dilution" in flags:
        res.risk_add += 15
        res.risk_reasons.append("דווח על הנפקת מניות – לחץ מכירות ודילול")
    if "distress" in flags:
        res.risk_add += 20
        res.risk_reasons.append("סימני מצוקה פיננסית בחדשות")
    if "legal" in flags:
        res.risk_add += 5
        res.risk_reasons.append("חקירה או תביעה פתוחה")
    return res, flags


def catalysts_signal(earnings_date: date | None, rev_score: float, flags: set[str], today: date,
                     blackout_days: int) -> SignalResult:
    res = SignalResult("catalysts")
    s, active = 0.0, False
    if earnings_date:
        days = (earnings_date - today).days
        res.data["earnings_in_days"] = days
        if 0 <= days <= blackout_days:
            res.risk_add += 10
            res.risk_reasons.append(f"דוח רבעוני בעוד {days} ימים – אירוע תנודתי")
        if 2 <= days <= 30:
            active = True
            if rev_score > 0.2:
                s += 0.4
                res.reasons.append(f"דוח רבעוני בעוד {days} ימים, והאנליסטים מעלים תחזיות לקראתו")
            elif rev_score < -0.2:
                s -= 0.3
                res.reasons.append(f"דוח רבעוני בעוד {days} ימים, והתחזיות יורדות לקראתו")
    if "pdufa" in flags:
        s += 0.2
        active = True
        res.risk_add += 10
        res.reasons.append("צפויה החלטת FDA על תרופה (PDUFA)")
        res.risk_reasons.append("החלטת FDA יכולה להזיז את המניה עשרות אחוזים לכל כיוון")
    for flag, delta, txt in (("fda_positive", 0.5, "התקדמות חיובית מול ה־FDA או בניסוי קליני"),
                             ("fda_negative", -0.6, "כישלון בניסוי קליני או מול ה־FDA"),
                             ("mna", 0.4, "דיווחים על עסקת רכישה או מיזוג"),
                             ("contract", 0.3, "חוזה או הסכם משמעותי חדש")):
        if flag in flags:
            s += delta
            active = True
            res.reasons.append(txt)
    res.score = clip(s)
    res.conf = 0.7 if active else 0.0
    return res
