"""Tests for the takeover check and SEC corporate events.  Run:  python -m unittest discover tests"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.signals.deals import (deal_check, deal_cluster, events_signal, norm_form, parse_filings,  # noqa: E402
                                 pinned_after_jump, price_reaction, takeover_headline)

ASOF = date(2026, 10, 9)


def recent_block(rows):
    """rows: [(form, 'YYYY-MM-DD', items)] -> SEC 'filings.recent' layout (newest first)."""
    rows = sorted(rows, key=lambda r: r[1], reverse=True)
    return {"form": [r[0] for r in rows], "filingDate": [r[1] for r in rows], "items": [r[2] for r in rows],
            "accessionNumber": [f"0000-26-{i:06d}" for i in range(len(rows))]}


def series(jump_day: str | None, jump: float = 0.22, pinned: bool = True, n: int = 260, seed: int = 3,
           end: str = "2026-10-09") -> pd.Series:
    """Daily closes with ~2.5% daily swings; optionally a one-day jump and a calm, pinned price afterwards."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    r = rng.normal(0.0005, 0.025, n)
    if jump_day:
        k = int(idx.searchsorted(pd.Timestamp(jump_day)))
        r[k] = jump
        if pinned:
            r[k + 1:] = rng.normal(0, 0.003, n - k - 1)
    return pd.Series(20 * np.exp(np.cumsum(r)), index=idx)


# RXO, 30/09-09/10/2026: C.H. Robinson's cash-and-stock offer was announced on 05/10
RXO_TAIL = [20.46, 21.36, 23.38, 28.65, 28.54, 28.36, 28.93, 29.05]


def rxo_like() -> pd.Series:
    s = series(None, n=130, seed=7)
    s = s / s.iloc[-9] * 20.2
    s.iloc[-8:] = RXO_TAIL
    return s


class TestFilings(unittest.TestCase):
    def test_norm_form(self):
        self.assertEqual(norm_form("SC TO-T"), ("SCTOT", False))
        self.assertEqual(norm_form("SCHEDULE 14D9/A"), ("SC14D9", True))
        self.assertEqual(norm_form("8-K"), ("8K", False))
        self.assertEqual(norm_form("NT 10-Q"), ("NT10Q", False))
        self.assertEqual(norm_form("424B5"), ("424B5", False))

    def test_parse_filings(self):
        f = parse_filings(recent_block([("8-K", "2026-10-06", "1.01,7.01,9.01"), ("425", "2026-10-05", ""),
                                        ("10-Q", "2026-08-06", ""), ("bad", "not-a-date", "")]))
        self.assertEqual([x["f"] for x in f], ["10Q", "425", "8K"])  # sorted oldest first, bad rows skipped
        self.assertEqual(f[-1]["items"], {"1.01", "7.01", "9.01"})
        self.assertEqual(parse_filings(None), [])
        self.assertEqual(parse_filings({"form": ["4"], "filingDate": []}), [])

    def test_cluster_keeps_latest_deal_and_cash_merger_proxy(self):
        f = parse_filings(recent_block([("425", "2025-11-01", ""), ("425", "2026-10-05", ""),
                                        ("425", "2026-10-07", "")]))
        cl = deal_cluster(f, ASOF)
        self.assertEqual([x["date"] for x in cl], [date(2026, 10, 5), date(2026, 10, 7)])
        # DEFA14A counts only next to an 8-K material agreement (cash merger announcement)
        cash = parse_filings(recent_block([("8-K", "2026-10-05", "1.01,8.01,9.01"), ("DEFA14A", "2026-10-05", "")]))
        self.assertEqual(len(deal_cluster(cash, ASOF)), 1)
        annual = parse_filings(recent_block([("DEFA14A", "2026-04-02", ""), ("DEF 14A", "2026-04-01", "")]))
        self.assertEqual(deal_cluster(annual, ASOF), [])


class TestPrice(unittest.TestCase):
    def test_price_reaction(self):
        s = rxo_like()
        react = price_reaction(s, date(2026, 10, 5))
        self.assertGreater(react["jump"], 0.3)
        self.assertGreater(react["day_jump"], 0.2)
        self.assertEqual(price_reaction(None, ASOF), {})
        self.assertEqual(price_reaction(s.iloc[:10], ASOF), {})

    def test_pinned_after_jump(self):
        pin = pinned_after_jump(rxo_like())
        self.assertIsNotNone(pin)
        self.assertEqual(pin["date"], date(2026, 10, 5))
        self.assertAlmostEqual(pin["jump"], 28.65 / 23.38 - 1, places=4)
        # a jump that keeps trading wildly afterwards is not "pinned"
        self.assertIsNone(pinned_after_jump(series("2026-09-25", pinned=False)))
        # no big jump at all
        self.assertIsNone(pinned_after_jump(series(None)))
        # with volume: a jump on normal volume is not an offer, a jump on 5x volume is
        s = rxo_like()
        k = s.pct_change().iloc[-60:].idxmax()
        quiet = pd.Series(1e6, index=s.index)
        self.assertIsNone(pinned_after_jump(s, volumes=quiet))
        loud = quiet.copy()
        loud[k] = 5e6
        self.assertIsNotNone(pinned_after_jump(s, volumes=loud))


class TestHeadlines(unittest.TestCase):
    def _hit(self, title, ticker="RXO", name="RXO, Inc."):
        pub = datetime(2026, 10, 5, 13, tzinfo=timezone.utc)
        return takeover_headline([{"title": title, "published": pub}], ticker, name, ASOF)

    def test_target_headlines(self):
        for t in ("C.H. Robinson to acquire RXO in $4.8 billion cash-and-stock deal",
                  "RXO agrees to be acquired by C.H. Robinson",
                  "C.H. Robinson agrees to buy rival RXO",
                  "Takeover bid for RXO lifts trucking stocks",
                  "RXO accepts $30.25 per share offer from C.H. Robinson",
                  "Example Corp to be taken private by Apollo"):
            name = "Example Corp" if "Example" in t else "RXO, Inc."
            self.assertIsNotNone(self._hit(t, name=name), t)

    def test_not_target_headlines(self):
        for t in ("RXO to acquire Coyote Logistics from UPS",            # RXO is the buyer
                  "Is it time to buy RXO stock?",
                  "Vanguard Group Inc. Acquires 12,345 Shares of RXO, Inc.",
                  "Hedge fund acquires RXO shares",
                  "RXO beats estimates, raises guidance",
                  "rxo trucking volumes rise"):                         # ticker matches only in capitals
            self.assertIsNone(self._hit(t), t)
        # a company named after a common word is matched only when capitalized
        self.assertIsNone(self._hit("Retailers plan to acquire target customers online", "TGT", "Target Corporation"))
        self.assertIsNotNone(self._hit("Activist pushes for takeover of Target", "TGT", "Target Corporation"))

    def test_old_headline_ignored(self):
        old = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertIsNone(takeover_headline([{"title": "X Corp to acquire RXO", "published": old}], "RXO", "RXO, Inc.",
                                            ASOF))


class TestDealCheck(unittest.TestCase):
    MERGER = recent_block([("8-K", "2026-10-06", "1.01,7.01,9.01"), ("425", "2026-10-05", ""),
                           ("425", "2026-10-07", ""), ("10-Q", "2026-08-06", "")])

    def test_target_is_excluded(self):
        d = deal_check(parse_filings(self.MERGER), rxo_like(), [], "RXO", "RXO, Inc.", ASOF)
        self.assertEqual(d["status"], "target")
        self.assertTrue(d["exclude"])
        self.assertEqual(d["date"], date(2026, 10, 5))
        self.assertIn("425", d["text"])
        self.assertIn("מוגבל", d["text"])

    def test_acquirer_is_only_flagged(self):
        flat = pd.Series(np.linspace(80, 76, 130), index=pd.bdate_range(end="2026-10-09", periods=130))
        d = deal_check(parse_filings(self.MERGER), flat, [], "CHRW", "C.H. Robinson Worldwide, Inc.", ASOF)
        self.assertEqual(d["status"], "involved")
        self.assertFalse(d["exclude"])

    def test_completed_deal(self):
        f = parse_filings(recent_block([("425", "2026-06-02", ""), ("8-K", "2026-10-01", "1.02,2.01,3.01,5.01")]))
        s = series("2026-06-02", n=200)
        d = deal_check(f, s, [], "ABC", "Abc Industries", ASOF)
        self.assertEqual(d["status"], "completed")
        self.assertTrue(d["exclude"])

    def test_terminated_deal(self):
        f = parse_filings(recent_block([("425", "2026-06-02", ""), ("425", "2026-06-20", ""),
                                        ("8-K", "2026-09-15", "1.02,8.01")]))
        d = deal_check(f, series("2026-06-02", pinned=False, n=200), [], "ABC", "Abc Industries", ASOF)
        self.assertEqual(d["status"], "terminated")
        self.assertFalse(d["exclude"])

    def test_headline_only_warns(self):
        news = [{"title": "Big Co to acquire Abc Industries for $40 a share",
                 "published": datetime(2026, 10, 1, tzinfo=timezone.utc)}]
        d = deal_check([], series(None), news, "ABC", "Abc Industries Inc.", ASOF)
        self.assertEqual(d["status"], "suspect")
        self.assertFalse(d["exclude"])

    def test_headline_with_price_jump(self):
        news = [{"title": "Big Co to acquire Abc Industries for $40 a share",
                 "published": datetime(2026, 10, 7, 12, tzinfo=timezone.utc)}]
        d = deal_check([], series("2026-10-07", pinned=False), news, "ABC", "Abc Industries Inc.", ASOF)
        self.assertEqual(d["status"], "target")
        self.assertTrue(d["exclude"])
        self.assertIn("Big Co", d["text"])

    def test_pinned_price_only_warns(self):
        d = deal_check([], series("2026-09-21"), [], "ABC", "Abc Industries", ASOF)
        self.assertEqual(d["status"], "suspect")
        self.assertFalse(d["exclude"])

    def test_nothing(self):
        d = deal_check(parse_filings(recent_block([("10-Q", "2026-08-06", "")])), series(None), [], "ABC", "Abc", ASOF)
        self.assertIsNone(d["status"])
        self.assertFalse(d["exclude"])


class TestEvents(unittest.TestCase):
    def test_events_signal(self):
        f = parse_filings(recent_block([("424B5", "2026-10-01", ""), ("NT 10-Q", "2026-08-20", ""),
                                        ("8-K", "2026-09-01", "4.02,9.01"), ("8-K", "2026-09-10", "3.01")]))
        ev = events_signal(f, 5e8, ASOF)
        self.assertEqual(ev.score, 0.0)
        self.assertEqual(ev.conf, 0.0)
        self.assertEqual(set(ev.data["flags"]), {"offering", "late", "restatement", "listing"})
        self.assertEqual(ev.risk_add, 12 + 15 + 20 + 10)
        self.assertEqual(len(ev.risk_reasons), 4)

    def test_big_company_debt_prospectus_ignored(self):
        f = parse_filings(recent_block([("424B5", "2026-10-01", "")]))
        self.assertEqual(events_signal(f, 2e11, ASOF).data["flags"], [])

    def test_old_and_missing(self):
        f = parse_filings(recent_block([("424B5", "2026-06-01", "")]))
        self.assertEqual(events_signal(f, 5e8, ASOF).data["flags"], [])
        self.assertEqual(events_signal(None, None, ASOF).risk_add, 0)

    def test_exchange_notice_skipped_when_deal_closes(self):
        f = parse_filings(recent_block([("8-K", "2026-10-01", "2.01,3.01,5.01")]))
        self.assertNotIn("listing", events_signal(f, 5e8, ASOF, "completed").data["flags"])


class TestDemoScan(unittest.TestCase):
    def test_takeover_target_not_picked(self):
        from radar.export import scan_json
        from radar.pipeline import run_scan
        from radar.settings import load_settings
        from radar.sources.demo import DEAL_TICKER, OFFER_TICKER

        with tempfile.TemporaryDirectory() as d:
            s = load_settings(root=ROOT)
            s.cfg["general"]["data_dir"] = str(Path(d) / "data")
            s.root = Path(d)
            res = run_scan(s, demo=True)
            by = {r["ticker"]: r for r in res["results"]}
            self.assertIn(DEAL_TICKER, by)
            target = by[DEAL_TICKER]
            self.assertEqual(target["deal"]["status"], "target")
            self.assertTrue(target["exclude"])
            self.assertFalse(target["pick"])
            self.assertTrue(target["why"]["cons"][0].startswith("בתהליך רכישה"))
            self.assertNotIn(DEAL_TICKER, [r["ticker"] for r in res["picks"]])
            self.assertIn("offering", by[OFFER_TICKER]["flags"])
            self.assertEqual(res["health"]["n"], len(res["results"]))
            data = scan_json(res)
            self.assertIn("health", data)
            row = next(r for r in data["results"] if r["ticker"] == DEAL_TICKER)
            for key in ("exclude", "deal", "flags", "rsi", "ext50", "target_mean", "filings_checked"):
                self.assertIn(key, row)
            # replaying a day before the announcement: no deal yet
            early = run_scan(s, demo=True, asof=date.today() - timedelta(days=21), tickers=[DEAL_TICKER])
            self.assertIsNone(early["results"][0]["deal"])


if __name__ == "__main__":
    unittest.main()
