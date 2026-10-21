"""Public Telegram channels (ביזנס 10, המשקיע ששוקע, מבזקהון): which stocks the Israeli public is reading about.

A public channel shows its latest posts on the web at t.me/s/<name>, without logging in, so the daily scan reads
that page directly: no Telegram account, no API keys, no session. Older posts are reached with ?before=<post id>.

Most posts name companies in Hebrew transliteration (אנבידיה, נטפליקס), some in English (SpaceX). A stock counts as
mentioned when a post has its Hebrew name, its English name, or its ticker written as $TICKER / (TICKER) / TICKER.
This is shown next to the stock and checked by the self-review; it is not part of the score: a Hebrew summary of
news that was already published in English usually comes after the market has moved."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone

from ..net import Http
from .rss import short_name

log = logging.getLogger("radar.telegram")

BASE = "https://t.me/s/"
HEB = "֐-׿"

# Hebrew names of companies that Israeli channels write about (US-listed, including Israeli dual listings).
# A name marked with '=' is a common Hebrew word too, so it only counts on its own (no attached ו/ה/ב/ל/מ/ש/כ).
HEB_NAMES = {
    "NVDA": ["אנבידיה", "אנווידיה", "נבידיה", "אנוידיה"], "AAPL": ["אפל"], "MSFT": ["מיקרוסופט"],
    "GOOGL": ["גוגל", "אלפבית"], "AMZN": ["אמזון"], "META": ["מטא", "פייסבוק"], "TSLA": ["טסלה"],
    "NFLX": ["נטפליקס"], "AVGO": ["ברודקום"], "AMD": ["איי.אם.די", "אי.אם.די", "איי אם די"], "INTC": ["אינטל"],
    "QCOM": ["קוואלקום", "קואלקום"], "ORCL": ["אורקל"], "CRM": ["סיילספורס"], "ADBE": ["אדובי"],
    "IBM": ["איי.בי.אם", "יבמ"], "CSCO": ["סיסקו"], "PLTR": ["פלנטיר", "פאלנטיר"], "MU": ["מיקרון"],
    "TSM": ["טייוואן סמיקונדקטור", "טיוואן סמיקונדקטור"], "SMCI": ["סופרמיקרו", "סופר מיקרו"],
    "SNOW": ["סנופלייק"], "UBER": ["אובר"], "ABNB": ["איירביאנבי", "אייר בי אנד בי"], "SHOP": ["שופיפיי"],
    "SPOT": ["ספוטיפיי"], "PYPL": ["פייפאל"], "COIN": ["קוינבייס"], "MSTR": ["מיקרוסטרטג'י"],
    "HOOD": ["רובינהוד"], "DIS": ["דיסני"], "WMT": ["וולמארט"], "COST": ["קוסטקו"],
    "HD": ["הום דיפו"], "NKE": ["נייקי"], "SBUX": ["סטארבקס"], "MCD": ["מקדונלדס"],
    "KO": ["קוקה קולה", "קוקה-קולה"], "PEP": ["פפסיקו"], "PG": ["פרוקטר אנד גמבל"],
    "JNJ": ["ג'ונסון אנד ג'ונסון"], "PFE": ["פייזר"], "MRNA": ["מודרנה"], "LLY": ["אלי לילי"],
    "NVO": ["נובו נורדיסק"], "UNH": ["יונייטדהלת'", "יונייטד הלת'"], "ABBV": ["אבווי"],
    "JPM": ["ג'יי פי מורגן", "ג'יי.פי מורגן", "ג'יי.פי. מורגן", "ג'י.פי מורגן"], "GS": ["גולדמן זאקס", "גולדמן סאקס"],
    "MS": ["מורגן סטנלי"], "BAC": ["בנק אוף אמריקה"], "C": ["סיטיגרופ", "סיטיבנק"], "WFC": ["וולס פארגו"],
    "MA": ["מאסטרקארד"], "BRK-B": ["ברקשייר"], "XOM": ["אקסון מוביל", "אקסון"], "CVX": ["=שברון"],
    "BA": ["בואינג"], "LMT": ["לוקהיד מרטין"], "RTX": ["ריית'און"], "GE": ["ג'נרל אלקטריק"],
    "GM": ["ג'נרל מוטורס"], "F": ["פורד"], "CAT": ["קטרפילר"], "INTU": ["אינטואיט"], "NOW": ["סרביסנאו"],
    "PANW": ["פאלו אלטו"], "CRWD": ["קראודסטרייק"], "ZS": ["זיסקיילר"], "NET": ["קלאודפלייר"],
    "DDOG": ["דאטאדוג"], "AMAT": ["אפלייד מטיריאלס"], "TXN": ["טקסס אינסטרומנטס"],
    "BABA": ["עליבאבא", "אליבאבא"], "PDD": ["טמו"], "RIVN": ["ריביאן"], "LCID": ["לוסיד"],
    "GME": ["גיימסטופ"], "RDDT": ["רדיט"], "SNAP": ["סנאפ", "סנאפצ'ט"], "PINS": ["פינטרסט"],
    "CBRL": ["קרקר בארל"], "MBLY": ["מובילאיי"], "TEVA": ["=טבע"], "ESLT": ["אלביט"],
    "CHKP": ["צ'ק פוינט", "צ'קפוינט"], "WIX": ["וויקס"], "MNDY": ["מאנדיי"], "NICE": ["נייס"],
    "CYBR": ["סייברארק"], "GLBE": ["גלובל-אי"], "FVRR": ["פייבר"], "TSEM": ["טאואר"], "CAMT": ["קמטק"],
    "ORA": ["אורמת"], "ENLT": ["אנלייט"], "SEDG": ["סולאראדג'", "סולאר אדג'"], "INMD": ["אינמוד"],
    "ZIM": ["צים"], "LMND": ["למונייד"], "CLBT": ["סלברייט"], "PAYO": ["פיוניר"], "INVZ": ["אינוויז"],
    "SPNS": ["ספיינס"], "NNOX": ["נאנו-אקס", "נאנו אקס"], "KRNT": ["קורנית"], "ICL": ["איי.סי.אל"],
}
# English names that differ from the company's legal name
EN_ALIASES = {"TSMC": "TSM", "Nvidia": "NVDA", "Google": "GOOGL", "Alphabet": "GOOGL", "Facebook": "META",
              "Instagram": "META", "JPMorgan": "JPM", "Temu": "PDD", "Strategy": "MSTR", "Snapchat": "SNAP"}
# capital-letter words that are not tickers in a Hebrew finance post
NOT_TICKERS = {"CEO", "CFO", "CTO", "COO", "USA", "ETF", "IPO", "GDP", "CPI", "PPI", "FED", "SEC", "IMF", "OPEC",
               "NASA", "NATO", "API", "EPS", "ESG", "USD", "EUR", "ILS", "BTC", "ETH", "NYSE", "WTI", "LNG", "FDA",
               "GPU", "CPU", "ALL", "NOW", "ONE", "ARE", "FOR", "NEW", "OPEN", "BIG", "LOW", "KEY", "GOOD", "HAS",
               "CAT", "PAY", "EAT", "SEE", "RUN", "WAY", "AIR", "CAR", "TOP", "HOT", "FUN", "GPS", "ATM", "IRS",
               "UAE", "EU", "UK", "AI", "TV", "PC", "EV", "IT", "OIL", "BUY", "SELL", "HOLD",
               # words that also show up in general news posts (המשקיע ששוקע writes about politics too)
               "ICE", "IDF", "WHO", "BBC", "CNN", "FBI", "CIA", "NYC", "GOP", "DOGE", "NEWS", "LIVE", "LIFE", "LOVE",
               "PLAY", "FAST", "REAL", "BEST", "TRUE", "HOPE", "SAFE", "MAIN", "CASH", "GAIN", "MORE", "WOW", "BIO"}


def channel_name(ch) -> str:
    """'@name', 't.me/name' or 'https://t.me/s/name' -> 'name'."""
    return str(ch).strip().rstrip("/").lstrip("@").split("/")[-1]


def newest_first(posts: list[dict]) -> list[dict]:
    """Posts of several channels in one list, newest first (posts without a date last)."""
    old = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(posts, key=lambda p: p["date"] or old, reverse=True)


def _name_pattern(name: str) -> re.Pattern:
    if name.startswith("="):  # a common Hebrew word: only on its own
        n = re.escape(name[1:])
        return re.compile(rf"(?<![{HEB}A-Za-z]){n}(?![{HEB}A-Za-z])")
    n = re.escape(name)  # Hebrew prefixes (ו, ה, ב, ל, מ, ש, כ) may be attached to the name
    return re.compile(rf"(?<![{HEB}A-Za-z])[והבלמשכ]{{0,2}}{n}(?![{HEB}A-Za-z])")


_HEB_PATTERNS = {t: [_name_pattern(n) for n in names] for t, names in HEB_NAMES.items()}


def _clean(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", fragment)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def parse_page(text: str, channel: str) -> list[dict]:
    """Posts of one t.me/s/<channel> page: id, date (UTC), text and link."""
    out = []
    for block in text.split('class="tgme_widget_message_wrap')[1:]:
        m = re.search(r'data-post="([^"/]+)/(\d+)"', block)
        if not m:
            continue
        body = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        if not body:
            continue
        try:
            d = datetime.fromisoformat(when.group(1)) if when else None
            if d is not None and d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
        except ValueError:
            d = None
        pid = int(m.group(2))
        out.append({"id": pid, "date": d, "text": _clean(body.group(1)), "url": f"https://t.me/{m.group(1)}/{pid}",
                    "channel": channel})
    return out


def fetch_channel(http: Http, channel: str, days: int = 7, max_pages: int = 12) -> list[dict]:
    """Posts of the last `days` days (newest first). Stops early when the page has nothing older."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    posts: dict[int, dict] = {}
    before = None
    for _ in range(max_pages):
        params = {"before": before} if before else None
        page = http.get(BASE + channel, params=params, ttl=1800, retries=1)
        items = parse_page(page or "", channel)
        if not items:
            break
        for p in items:
            posts[p["id"]] = p
        oldest = min(items, key=lambda p: p["id"])
        if before is not None and oldest["id"] >= before:
            break
        before = oldest["id"]
        if oldest["date"] is not None and oldest["date"] < cutoff:
            break
    return sorted((p for p in posts.values() if p["date"] is None or p["date"] >= cutoff),
                  key=lambda p: p["id"], reverse=True)


def _patterns_for(ticker: str, name: str | None) -> list[re.Pattern]:
    pats = list(_HEB_PATTERNS.get(ticker, []))
    t = re.escape(ticker)
    pats.append(re.compile(rf"\${t}(?![A-Za-z])|\({t}\)"))
    if len(ticker) >= 3 and ticker not in NOT_TICKERS and "-" not in ticker:
        pats.append(re.compile(rf"(?<![A-Za-z$]){t}(?![A-Za-z])"))
    nm = short_name(name)
    if nm.isupper():   # SEC writes names in capitals; posts write them normally
        nm = nm.title()
    words = nm.split()
    if words:
        en = " ".join(words[:2]) if len(words) >= 2 else words[0]
        if len(en) >= 5 and en.upper() not in NOT_TICKERS:   # capitalised company name, as written in English
            pats.append(re.compile(rf"(?<![A-Za-z]){re.escape(en)}(?![A-Za-z])"))
    for alias, tk in EN_ALIASES.items():
        if tk == ticker:
            pats.append(re.compile(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])"))
    return pats


def mentions(posts: list[dict], ticker: str, name: str | None) -> list[dict]:
    """Posts that mention the stock, newest first: date, headline (first line, shortened) and link."""
    pats = _patterns_for(ticker, name)
    out = []
    for p in newest_first(posts):
        if any(pt.search(p["text"]) for pt in pats):
            head = p["text"].split("\n", 1)[0].strip() or p["text"][:140]
            out.append({"d": p["date"].isoformat() if p["date"] else None,
                        "t": head[:140] + ("…" if len(head) > 140 else ""), "u": p["url"], "c": p.get("channel")})
    return out
