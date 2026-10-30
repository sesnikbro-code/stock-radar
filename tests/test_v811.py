"""Tests for version 8.11: funds left out, volatility penalty, new weights, wrong prices fixed, gap exits rechecked.
Run:  python -m unittest discover tests"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.dataguard import clean_price_glitches  # noqa: E402
from radar.journal import Journal  # noqa: E402
from radar.pipeline import is_fund, vol_penalty  # noqa: E402
from radar.settings import load_settings  # noqa: E402
from radar.sources.sec import is_fund_name, parse_tickers_exchange  # noqa: E402
from radar.util import TickerData  # noqa: E402


class TestFunds(unittest.TestCase):
    def test_names(self):
        for n in ("United States Oil Fund, LP", "The Zcash ETF", "iShares Bitcoin Trust ETF", "SPDR Gold Trust",
                  "Grayscale Ethereum Trust", "Sprott Physical Silver Trust", "abrdn Gold ETF Trust",
                  "Western Asset High Income Fund II Inc."):
            self.assertTrue(is_fund_name(n), n)
        for n in ("Pebblebrook Hotel Trust", "Realty Income Corp", "Bitcoin Depot Inc.", "Fundamental Global Inc.",
                  "Northern Trust Corp", "Porch Group, Inc.", "Cracker Barrel Old Country Store, Inc", "Funding Circle"):
            self.assertFalse(is_fund_name(n), n)

    def test_universe_and_quote_type(self):
        data = {"fields": ["cik", "name", "ticker", "exchange"],
                "data": [[1, "United States Oil Fund, LP", "USO", "NYSE"], [2, "Porch Group, Inc.", "PRCH", "Nasdaq"],
                         [3, "The Zcash ETF", "ZCSH", "NYSE"]]}
        self.assertEqual([r["ticker"] for r in parse_tickers_exchange(data, [])], ["PRCH"])
        td = TickerData(ticker="XYZ", name="Some Name", cik=None, price=10.0, tech={}, prices=None)
        td.info = {"quoteType": "ETF"}
        self.assertTrue(is_fund(td))
        td.info = {"quoteType": "EQUITY"}
        self.assertFalse(is_fund(td))


class TestScoring(unittest.TestCase):
    def test_weights_and_penalty(self):
        s = load_settings(root=ROOT)
        w = s.get("weights")
        self.assertEqual((w["insider_buying"], w["volume_accumulation"], w["momentum"], w["macro"]), (0.5, 0.3, 1.3, 0.0))
        self.assertEqual(vol_penalty(0.02, s), 0)
        self.assertAlmostEqual(vol_penalty(0.0675, s), 4.0)
        self.assertEqual(vol_penalty(0.2, s), 8)
        self.assertEqual(vol_penalty(None, s), 0)


def _panel(closes, opens=None, lows=None):
    idx = pd.bdate_range("2026-10-01", periods=len(closes))
    c = pd.DataFrame({"X": closes}, index=idx, dtype=float)
    o = pd.DataFrame({"X": opens if opens else closes}, index=idx, dtype=float)
    lo = pd.DataFrame({"X": lows if lows else [v * 0.99 for v in closes]}, index=idx, dtype=float)
    return idx, {"Close": c, "Open": o, "High": c * 1.01, "Low": lo}


class TestPenaltyInScoreMode(unittest.TestCase):
    def test_combined_score_gets_the_penalty(self):
        from radar.pipeline import run_scan
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(root=ROOT)
            s.cfg["general"]["data_dir"] = str(Path(d) / "data")
            s.root = Path(d)
            res = run_scan(s, demo=True)                      # no model: picks by the combined score
            self.assertEqual(res["pick_mode"], "score")
            hit = [r for r in res["results"] if r["vol_penalty"] > 0]
            self.assertTrue(hit)
            self.assertTrue(all(any("ירדו" in c for c in r["why"]["cons"]) for r in hit if r["vol_penalty"] >= 2))


class TestPriceGlitches(unittest.TestCase):
    def test_spike_open_and_low(self):
        idx, p = _panel([10, 10.1, 3.0, 10.2, 10.3, 10.2], opens=[10, 10.1, 10, 10.2, 2.9, 10.2],
                        lows=[9.9, 10, 2.9, 10.1, 2.8, 6.0])
        out, n = clean_price_glitches(p)
        self.assertEqual(n["spike"], 1)                                  # day 3: 3.0 and straight back
        self.assertAlmostEqual(out["Close"]["X"].iloc[2], 10.1)
        self.assertAlmostEqual(out["Open"]["X"].iloc[4], 10.3)           # an open of 2.9 on a 10.3 day
        self.assertGreater(out["Low"]["X"].iloc[4], 10)                  # ... and its low
        self.assertGreater(out["Low"]["X"].iloc[5], 10)                  # a low of 6.0 on a calm 10.2 day

    def test_real_crash_is_kept(self):
        idx, p = _panel([10, 10.1, 6.0, 5.9, 6.1], opens=[10, 10.1, 6.2, 6.0, 5.9], lows=[9.9, 10, 5.8, 5.7, 5.8])
        out, n = clean_price_glitches(p)
        self.assertEqual(sum(n.values()), 0)
        self.assertAlmostEqual(out["Close"]["X"].iloc[2], 6.0)


class TestGapExitRecheck(unittest.TestCase):
    def test_wrong_open_does_not_close_the_trade(self):
        idx, p = _panel([4.3, 4.25, 4.2, 4.15, 4.1, 4.12, 4.15], opens=[4.3, 4.25, 4.2, 4.15, 2.95, 4.12, 4.15],
                        lows=[4.25, 4.2, 4.15, 4.1, 2.95, 4.08, 4.1])
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "j.sqlite")
            j.conn.execute("INSERT INTO paper (ticker, entry_date, entry_price, initial_stop, stop, atr, shares, status, "
                           "exit_date, exit_price, exit_reason, last_price) VALUES ('X', ?, 4.3, 3.835, 3.835, 0.2, 100, "
                           "'closed', ?, 2.95, 'stop', 2.95)", (idx[0].date().isoformat(), idx[4].date().isoformat()))
            j.conn.commit()
            j.paper_update(p, idx[-1].date(), {"max_hold_days": 90}, 2.5, 3.0)   # the raw prices: still a gap exit
            self.assertEqual(j._rows()[0]["exit_reason"], "gap")
            clean, _ = clean_price_glitches(p)
            j.paper_update(clean, idx[-1].date(), {"max_hold_days": 90}, 2.5, 3.0)
            row = j._rows()[0]
            self.assertEqual(row["status"], "open")                           # reopened: the stop was never reached
            self.assertIsNone(row["exit_price"])
            self.assertAlmostEqual(row["last_price"], 4.15)
            j.close()


if __name__ == "__main__":
    unittest.main()
