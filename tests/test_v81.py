"""Tests for version 8.1: model-driven picks, exit-plan tournament, data guard, free news feeds, paper fixes.
Run:  python -m unittest discover tests"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.journal import Journal, plan_from_text, plan_text  # noqa: E402
from radar.scoring import position_plan  # noqa: E402
from radar.settings import load_settings  # noqa: E402
from radar.train import simulate_trades  # noqa: E402


def _settings(d):
    s = load_settings(root=ROOT)
    s.cfg["general"]["data_dir"] = str(Path(d) / "data")
    s.cfg["general"]["reports_dir"] = str(Path(d) / "reports")
    s.root = Path(d)
    return s


class TestExitPlans(unittest.TestCase):
    def _one(self, path, **kw):
        c = np.asarray(path, dtype=float)[:, None]
        o = np.vstack([c[:1], c[:-1]])
        h, lo = c * 1.005, c * 0.995
        atr = np.full_like(c, 1.0)
        return simulate_trades(o, h, lo, c, atr, [0], len(c) - 2, **kw)

    def test_trailing_stop_keeps_the_gain(self):
        up = list(np.linspace(100, 130, 30)) + list(np.linspace(130, 110, 20)) + [110] * 20
        fixed = self._one(up, stop_atr=2.5, target_r=None)
        trail = self._one(up, stop_atr=2.5, target_r=None, trail_atr=3.0)
        self.assertEqual(int(trail["kind"][0, 0]), -1)                 # left by the trailing stop ...
        self.assertGreater(trail["ret"][0, 0], 0.2)                    # ... near the top, with most of the gain
        self.assertLess(trail["days"][0, 0], 40)
        self.assertEqual(int(fixed["kind"][0, 0]), 0)                  # the fixed stop never moved: time exit
        self.assertLess(fixed["ret"][0, 0], trail["ret"][0, 0])

    def test_no_target_holds_to_the_end(self):
        res = self._one(np.linspace(100, 160, 70), stop_atr=5.0, target_r=None)
        self.assertEqual(int(res["kind"][0, 0]), 0)
        self.assertEqual(int(res["days"][0, 0]), 68)
        self.assertGreater(res["ret"][0, 0], 0.5)

    def test_plan_text_roundtrip_and_position_plan(self):
        pl = {"stop_atr": 2.5, "target_r": None, "window": 63, "trail_atr": 3.0}
        self.assertEqual(plan_from_text(plan_text(pl)), pl)
        self.assertIsNone(plan_from_text(None))
        risk = {"account_size": 50000, "risk_per_trade_pct": 1.0, "atr_stop_multiple": 2.5, "target_r": 3.0,
                "max_position_pct": 10}
        p = position_plan(100.0, 2.0, risk, pl)
        self.assertAlmostEqual(p["stop"], 95.0)
        self.assertIsNone(p["target"])
        self.assertAlmostEqual(p["trail_pct"], 0.06)
        base = position_plan(100.0, 2.0, risk)
        self.assertAlmostEqual(base["target"], 115.0)
        self.assertEqual(base["days"], 63)


class TestPaperFixes(unittest.TestCase):
    def _panel(self):
        idx = pd.bdate_range("2026-03-02", periods=12)
        c = pd.Series(np.linspace(20, 23, 12), index=idx)
        return idx, {"Close": pd.DataFrame({"X": c}), "Open": pd.DataFrame({"X": c - 0.1}),
                     "High": pd.DataFrame({"X": c + 0.2}), "Low": pd.DataFrame({"X": c - 0.2})}

    def test_fills_on_the_day_after_the_prices_and_keeps_last_price(self):
        idx, panel = self._panel()
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "j.sqlite")
            # scan on the morning of idx[4] (UTC) used the prices of idx[3]: the order fills at idx[4]'s open
            j.paper_open([{"ticker": "X", "price": 20.8, "atr": 0.5, "plan": {"stop": 19.0, "shares": 10}}],
                         idx[4].date(), 10, bar_date=idx[3].date())
            part = {k: v.iloc[:6] for k, v in panel.items()}
            j.paper_update(part, idx[5].date(), {"max_hold_days": 90}, 2.5, 3.0)
            row = j._rows()[0]
            self.assertEqual(row["entry_date"], idx[4].date().isoformat())
            self.assertAlmostEqual(row["entry_price"], float(panel["Open"]["X"].iloc[4]))
            last = row["last_price"]
            self.assertAlmostEqual(last, float(panel["Close"]["X"].iloc[5]))
            j.paper_update(part, idx[5].date(), {"max_hold_days": 90}, 2.5, 3.0)   # a weekend rescan
            self.assertAlmostEqual(j._rows()[0]["last_price"], last)               # not reset to the entry
            j.close()

    def test_new_positions_per_day_and_plan_days(self):
        idx, panel = self._panel()
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "j.sqlite")
            picks = [{"ticker": t, "price": 20.0, "atr": 0.5, "plan": {"stop": 18.0, "shares": 10}} for t in "ABCDE"]
            n = j.paper_open(picks, idx[1].date(), 20, bar_date=idx[0].date(), max_new=3,
                             plan={"stop_atr": 4.0, "target_r": None, "window": 5, "trail_atr": None})
            self.assertEqual(n, 3)
            self.assertEqual(j._rows()[0]["plan"], "4||5|")
            j.conn.execute("DELETE FROM paper WHERE ticker != 'A'")
            j.conn.execute("UPDATE paper SET ticker='X'")
            j.paper_update(panel, idx[-1].date(), {"max_hold_days": 90}, 2.5, 3.0)
            row = j._rows()[0]
            self.assertEqual(row["exit_reason"], "time")                           # the plan's 5 trading days
            self.assertEqual(row["exit_date"], idx[5].date().isoformat())
            j.close()

    def test_old_journal_gets_new_columns(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.sqlite"
            c = sqlite3.connect(path)
            c.execute("CREATE TABLE paper (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, entry_date TEXT, "
                      "entry_price REAL, initial_stop REAL, stop REAL, atr REAL, shares INT, status TEXT, exit_date TEXT, "
                      "exit_price REAL, exit_reason TEXT, last_price REAL)")
            c.commit()
            c.close()
            j = Journal(path)
            self.assertIn("plan", {r[1] for r in j.conn.execute("PRAGMA table_info(paper)")})
            self.assertIn("plan", {r[1] for r in j.conn.execute("PRAGMA table_info(predictions)")})
            j.close()


class TestDataGuardAndNews(unittest.TestCase):
    def test_data_quality(self):
        from radar.pipeline import data_quality
        ok = {"n": 100, "analysts": 95, "sector": 99, "filings": 100}
        self.assertEqual(data_quality(ok), [])
        self.assertTrue(data_quality({**ok, "analysts": 0, "sector": 0}))
        self.assertTrue(data_quality({**ok, "filings": 3}))
        self.assertEqual(data_quality({"n": 5, "analysts": 0}), [])

    def test_rss(self):
        from radar.sources.rss import Feeds, parse_rss, short_name
        self.assertEqual(short_name("Moderna, Inc."), "Moderna")
        self.assertEqual(short_name("HF Sinclair Corp"), "HF Sinclair")
        xml = """<rss><channel>
          <item><title>Moderna jumps on vaccine data - Reuters</title><link>https://x/1</link>
            <pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate><source url="https://reuters.com">Reuters</source></item>
          <item><title>Unrelated story - AP</title><link>https://x/2</link>
            <pubDate>Mon, 12 Oct 2026 09:00:00 GMT</pubDate><source url="https://ap.org">AP</source></item>
        </channel></rss>"""
        items = parse_rss(xml)
        self.assertEqual(items[0]["title"], "Moderna jumps on vaccine data")
        self.assertEqual(items[0]["source"], "Reuters")

        class FakeHttp:
            calls = 0

            def get(self, url, **kw):
                FakeHttp.calls += 1
                if "google" in url:
                    return xml
                raise RuntimeError("blocked")
        f = Feeds(FakeHttp(), max_fail=2)
        got = f.news("MRNA", "Moderna, Inc.", days=100000)
        self.assertEqual([i["title"] for i in got], ["Moderna jumps on vaccine data"])   # only about the company
        f2 = Feeds(FakeHttp(), max_fail=2)
        for _ in range(5):
            f2.news("ZZZ", "Zz", days=7)            # name too short for Google, Yahoo fails
        self.assertLessEqual(f2.state["yahoo"][0], 2)   # stopped asking after 2 failures


class TestModelPicks(unittest.TestCase):
    def test_train_then_model_driven_scan(self):
        from radar.export import _clean, scan_json
        from radar.model import ProbModel
        from radar.pipeline import run_scan
        from radar.train import TRAIN_REV, train_model

        with tempfile.TemporaryDirectory() as d:
            s = _settings(d)
            s.cfg["train"]["years"] = 4
            path = Path(d) / "model.json"
            meta = train_model(s, demo=True, out_path=path)
            v = meta["validation"]
            self.assertEqual(meta["train_rev"], TRAIN_REV)
            self.assertEqual(len(v["exit_plans"]), 5)
            self.assertEqual(sum(1 for x in v["exit_plans"] if x["chosen"]), 1)
            chosen = next(x for x in v["exit_plans"] if x["chosen"])
            base = v["exit_plans"][0]
            self.assertEqual(meta["plan"]["key"], chosen["key"])
            if chosen["key"] != "base":   # a new plan only with a clear margin in the final check
                self.assertGreaterEqual(chosen["fin_month"], base["fin_month"] + 0.002)
            self.assertEqual(len(v["rank_deciles"]), 10)
            self.assertTrue(0 < v["band"]["frac"] <= 0.1)
            m = ProbModel.load(path)
            lvl, hist = m.rank_bucket(0.999, 1.0)
            self.assertEqual(lvl, 10)
            self.assertTrue(hist["band"])
            res = run_scan(s, demo=True, model_path=path)
            self.assertEqual(res["pick_mode"], "model")
            self.assertTrue(all(r.get("vol_penalty", 0) == 0 for r in res["results"]))   # the model is not vetoed twice
            self.assertTrue(res["picks"])
            for r in res["picks"]:
                self.assertTrue(r["in_band"])
                self.assertGreaterEqual(r["upside"], 45)
                self.assertEqual(r["plan"]["days"], meta["plan"]["window"])
            per_sector = {}
            for r in res["picks"]:
                per_sector[r["sector"]] = per_sector.get(r["sector"], 0) + 1
            self.assertLessEqual(max(per_sector.values()), 3)
            scores = [r["model_score"] for r in res["picks"]]
            self.assertEqual(scores, sorted(scores, reverse=True))           # in the model's order
            self.assertEqual(res["results"][:len(res["picks"])], res["picks"])
            reasons = {r["why_not"].split(":")[0] for r in res["results"] if r["why_not"]}
            self.assertIn("band", reasons)
            j = scan_json(res)
            self.assertEqual(j["pick_mode"], "model")
            self.assertEqual(j["plan"]["key"], meta["plan"]["key"])
            self.assertTrue(all(x.get("exp_base") is not None for x in j["results"] if x["pick"]))
            json.dumps(_clean(j), allow_nan=False)
            # the self-review finding the model failing live sends the picks back to the combined score
            jr = Journal(s.data_dir / "journal_demo.sqlite")
            from radar.review import model_id
            jr.conn.execute("INSERT OR REPLACE INTO kv VALUES ('review_state', ?)",
                            (json.dumps({"model_id": model_id(m), "drift": "bad"}),))
            jr.conn.commit()
            jr.close()
            res2 = run_scan(s, demo=True, model_path=path)
            self.assertEqual(res2["pick_mode"], "score")


if __name__ == "__main__":
    unittest.main()
