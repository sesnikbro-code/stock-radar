"""Data-source facade. LiveSources talks to the real services; DemoSources (demo.py) produces
synthetic data so the whole system can be tried without internet or keys."""
from __future__ import annotations

import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

from ..features import merge_earnings
from ..net import Http
from . import countries, finnhub, fred, gdelt, llm, rss, sec, telegram, usaspending

log = logging.getLogger("radar.sources")


class LiveSources:
    is_demo = False

    def __init__(self, settings):
        self.s = settings
        self.http = Http(settings.cache_dir, settings.user_agent)
        self._market = None
        self._train_http = None
        self.feeds = rss.Feeds(self.http)
        if not settings.get("general.sec_email"):
            log.warning("לא הוגדר אימייל ל־SEC (sec_email בקובץ config.yaml או SEC_EMAIL ב־.env). "
                        "SEC עלולה לחסום בקשות בלי אימייל ליצירת קשר.")

    @property
    def market(self):
        if self._market is None:
            from .market import YahooMarket
            self._market = YahooMarket(self.s.cache_dir, max_expirations=self.s.get("options.max_expirations", 2))
        return self._market

    # ---------------------------------------------------------------- universe & prices
    def universe(self) -> list[dict]:
        try:
            listed = sec.load_universe(self.http, self.s.get("universe.exchanges", []))
        except Exception as e:  # noqa: BLE001
            log.warning("לא ניתן לטעון את רשימת המניות מ־SEC: %s", e)
            listed = []
        by_ticker = {r["ticker"]: r for r in listed}
        if self.s.get("universe.source") == "list" or not listed:
            wanted = [t.upper() for t in (self.s.get("universe.list") or [])]
            return [by_ticker.get(t, {"ticker": t, "cik": None, "name": t, "exchange": None}) for t in wanted]
        return listed

    def cik_lookup(self) -> dict[str, dict]:
        try:
            return {r["ticker"]: r for r in sec.load_universe(self.http, [])}
        except Exception:  # noqa: BLE001
            return {}

    def prices(self, tickers, period="2y", use_cache=True):
        return self.market.download_prices(list(tickers), period=period, use_cache=use_cache)

    @property
    def train_http(self) -> Http:
        """Big one-off downloads for model training go to a temporary folder, so the daily cloud cache stays small."""
        if self._train_http is None:
            self._train_http = Http(Path(tempfile.gettempdir()) / "radar_train_cache", self.s.user_agent, timeout=60)
        return self._train_http

    def details(self, ticker, price=None, want_options=True):
        return self.market.details(ticker, price, want_options)

    # ---------------------------------------------------------------- insiders

    def filings(self, cik: int) -> dict | None:
        """SEC filing list of one company (for the takeover check and corporate events)."""
        return sec.company_filings(self.http, int(cik))

    def company_sic(self, cik: int | None, http: Http | None = None) -> str | None:
        """Industry code (SIC) from SEC: same cached file as the insider and earnings checks."""
        if not cik:
            return None
        try:
            return sec.company_sic(http or self.http, int(cik))
        except Exception as e:  # noqa: BLE001
            log.debug("SIC failed %s: %s", cik, e)
            return None

    def insider(self, cik: int, lookback_days: int, max_filings: int = 40) -> list[dict]:
        return sec.company_form4(self.http, cik, lookback_days, max_filings)

    def insider_history(self, years: int, ciks: set[int] | None = None, log_fn=None):
        """Every open-market insider purchase/sale of the past years (SEC quarterly data sets)."""
        return sec.insider_history(self.train_http, years, ciks, log_fn)

    def earnings_events(self, ticker: str, cik: int | None, details: dict | None = None, since: date | None = None,
                        yahoo: bool = True, http: Http | None = None):
        """Quarterly report dates (SEC 8-K item 2.02) + EPS surprise (Yahoo). None if unknown."""
        since = since or date.today() - timedelta(days=200)
        sec_dates = None
        if cik:
            try:
                sec_dates = sec.earnings_filing_dates(http or self.http, int(cik), since)
            except Exception as e:  # noqa: BLE001
                log.debug("SEC earnings dates failed %s: %s", ticker, e)
        if details is not None:
            ydf = details.get("earnings_hist")
        else:
            ydf = self.market.earnings_history(ticker) if yahoo else None
        if ydf is not None and len(ydf):
            ydf = ydf[ydf["date"] >= since]
        return merge_earnings(sec_dates, ydf)

    def insider_discovery(self, days: int, max_filings: int) -> dict:
        try:
            return sec.discover_insider_buying(self.http, days, max_filings)
        except Exception as e:  # noqa: BLE001
            log.warning("סריקת קניות מנהלים בכל השוק נכשלה: %s", e)
            return {}

    # ---------------------------------------------------------------- context
    def gov_contracts(self, name: str, days: int):
        return usaspending.contract_awards(self.http, name, days)

    def macro_series(self):
        return fred.fetch_all(self.http, self.s.secret("FRED_API_KEY"))

    def themes(self):
        return gdelt.all_themes(self.http)

    def country_stats(self, iso: str):
        return countries.country_stats(self.http, iso)

    def country_conflict(self, name: str):
        return gdelt.country_conflict(self.http, name)

    # ---------------------------------------------------------------- news
    def extra_news(self, ticker: str, days: int, name: str | None = None, have: int = 0):
        """Finnhub (free key) and, when a stock still has no headlines, the free RSS feeds."""
        out = []
        if self.s.get("news.use_finnhub"):
            out = finnhub.company_news(self.http, self.s.secret("FINNHUB_API_KEY"), ticker, days)
        if not out and not have and self.s.get("news.use_rss", True):
            out = self.feeds.news(ticker, name, days)
        return out

    def telegram_posts(self, channels: list[str], days: int = 7, max_pages: int = 12) -> list[dict]:
        """Recent posts of public Telegram channels (read from t.me/s/<name>, no account needed)."""
        out = []
        for ch in channels or []:
            name = telegram.channel_name(ch)
            try:
                got = telegram.fetch_channel(self.http, name, days, max_pages)
                log.info("ערוץ הטלגרם %s: %d הודעות", name, len(got))
                out += got
            except Exception as e:  # noqa: BLE001
                log.warning("קריאת ערוץ הטלגרם %s נכשלה: %s", ch, e)
        return telegram.newest_first(out)

    def classify_news(self, ticker: str, name: str, news: list[dict]):
        if not self.s.get("news.use_llm"):
            return None
        return llm.classify_headlines(self.s.secret("ANTHROPIC_API_KEY"), self.s.get("news.llm_model"),
                                      ticker, name, news)


def get_sources(settings, demo: bool = False, asof=None):
    if demo:
        from .demo import DemoSources
        return DemoSources(settings, asof=asof)
    return LiveSources(settings)
