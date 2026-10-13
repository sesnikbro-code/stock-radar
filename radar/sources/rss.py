"""Free headline feeds that need no key: Google News search and Yahoo Finance RSS.

The yfinance news call often returns nothing for requests from cloud servers, so the daily scan had no news at
all. These feeds are tried when a stock has no headlines yet. If a feed keeps failing in a run, it is skipped for
the rest of that run instead of slowing the scan down."""
from __future__ import annotations

import logging
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from ..net import Http

log = logging.getLogger("radar.rss")

GOOGLE = "https://news.google.com/rss/search"
YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline"
_SUFFIX = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|"
                     r"n\.?v|s\.?a|ag|se|lp|llc|trust|class\s+[a-c]|the)\.?$", re.I)


def short_name(name: str | None) -> str:
    """'Moderna, Inc.' -> 'Moderna'. Used to search and to check that a headline is about the company."""
    s = re.sub(r"\s+", " ", str(name or "")).strip(" .,")
    for _ in range(3):
        s2 = _SUFFIX.sub("", s).strip(" .,")
        if s2 == s:
            break
        s = s2
    return s


def parse_rss(text: str) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        src = it.find("source")
        source = (src.text or "").strip() if src is not None else ""
        if source and title.endswith(" - " + source):      # Google appends the publisher to the title
            title = title[: -len(source) - 3].strip()
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "")
            if pub is not None and pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pub = None
        out.append({"title": title, "summary": re.sub(r"<[^>]+>", " ", it.findtext("description") or "").strip()[:400],
                    "published": pub, "source": source, "url": (it.findtext("link") or "").strip()})
    return out


class Feeds:
    """Both feeds with a per-run circuit breaker (shared by the scan's worker threads)."""

    def __init__(self, http: Http, max_fail: int = 6):
        self.http, self.max_fail = http, max_fail
        self.state = {"google": [0, 0], "yahoo": [0, 0]}   # [failures, successes]
        self.lock = threading.Lock()

    def _ok(self, feed: str) -> bool:
        f, ok = self.state[feed]
        return not (f >= self.max_fail and ok == 0)

    def _mark(self, feed: str, success: bool) -> None:
        with self.lock:
            self.state[feed][0 if not success else 1] += 1

    def _get(self, feed: str, url: str, params: dict) -> list[dict] | None:
        if not self._ok(feed):
            return None
        try:
            text = self.http.get(url, params=params, ttl=3 * 3600, retries=0)
        except Exception as e:  # noqa: BLE001
            log.debug("%s news failed: %s", feed, e)
            self._mark(feed, False)
            return None
        items = parse_rss(text or "")
        self._mark(feed, text is not None)
        return items

    def news(self, ticker: str, name: str | None, days: int = 7) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days + 1)
        nm = short_name(name)
        out: list[dict] = []
        if nm and len(nm) >= 3:
            items = self._get("google", GOOGLE, {"q": f'"{nm}" stock when:{int(days)}d', "hl": "en-US", "gl": "US",
                                                 "ceid": "US:en"}) or []
            key = nm.split()[0].lower()
            out += [i for i in items if key in i["title"].lower() or ticker.lower() in i["title"].lower().split()]
        if not out:
            out += self._get("yahoo", YAHOO, {"s": ticker, "region": "US", "lang": "en-US"}) or []
        return [i for i in out if i["published"] is None or i["published"] >= cutoff][:20]

    def summary(self) -> str:
        g, y = self.state["google"], self.state["yahoo"]
        return f"Google News {g[1]} הצלחות/{g[0]} כישלונות, Yahoo RSS {y[1]}/{y[0]}"
