"""Tests for the self-review (radar/review.py).  Run:  python -m unittest discover tests"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar import review  # noqa: E402
from radar.journal import Journal  # noqa: E402
from radar.settings import load_settings  # noqa: E402


class FakeModel:
    def __init__(self, meta=None):
        self.kind = "gbm"
        self.meta = {"trained": "2026-01-01T00:00:00+00:00", **(meta or {})}
        self.quality = 1.0


def _panel(paths: dict[str, np.ndarray], start="2026-01-02"):
    idx = pd.bdate_range(start, periods=len(next(iter(paths.values()))))
    c = pd.DataFrame(paths, index=idx)
    return {"Open": c.shift(1).fillna(c), "High": c * 1.01, "Low": c * 0.99, "Close": c,
            "Volume": pd.DataFrame(1e6, index=idx, columns=c.columns)}


class TestOutcomes(unittest.TestCase):
    def test_replays_the_plan_and_grades_after_21_days(self):
        n = 120
        up = np.concatenate([np.full(10, 20.0), np.linspace(20, 40, n - 10)])     # climbs to the target
        down = np.concatenate([np.full(10, 20.0), np.linspace(20, 12, n - 10)])   # falls to the stop
        flat = np.full(n, 20.0)
        panel = _panel({"UP": up, "DN": down, "FL": flat, "SPY": np.linspace(400, 420, n)})
        s = load_settings(root=ROOT)
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "j.sqlite")
            bar = panel["Close"].index[9].date()
            res = [{"ticker": t, "price": 20.0, "atr": 0.4, "pick": t != "FL", "upside": 70, "risk": 30,
                    "dollar_vol": 1e8, "why": {"pros": ["סיבה"]}, "signals": {}} for t in ("UP", "DN", "FL")]
            review.record(j, bar + timedelta(days=1), bar, res, None, None, 0.002)
            review.update_outcomes(j, panel, {}, s)
            o = {r[1]: r for r in j.conn.execute("SELECT * FROM outcomes")}
            cols = [c[1] for c in j.conn.execute("PRAGMA table_info(outcomes)")]
            get = lambda t, k: o[t][cols.index(k)]  # noqa: E731
            self.assertEqual(get("UP", "kind"), 1)               # target = entry + 3 x 2.5 x 0.4 = +3$
            self.assertEqual(get("UP", "status"), "closed")
            self.assertAlmostEqual(get("UP", "target") - get("UP", "entry"), 3.0, places=6)
            self.assertEqual(get("DN", "kind"), -1)
            self.assertLess(get("DN", "ret"), 0)
            self.assertEqual(get("FL", "status"), "closed")      # 63 days passed: time exit
            self.assertEqual(get("FL", "kind"), 0)
            for t in ("UP", "DN", "FL"):
                self.assertIsNotNone(get(t, "ret21"))
            self.assertGreaterEqual(get("UP", "mfe_r"), 3.0 - 1e-9)
            j.close()

    def test_open_trade_stays_open(self):
        n = 30
        panel = _panel({"X": np.full(n, 20.0), "SPY": np.full(n, 400.0)})
        s = load_settings(root=ROOT)
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "j.sqlite")
            bar = panel["Close"].index[20].date()
            review.record(j, bar, bar, [{"ticker": "X", "price": 20.0, "atr": 0.4, "pick": True, "upside": 70,
                                        "risk": 30, "signals": {}}], None, None, 0.002)
            review.update_outcomes(j, panel, {}, s)
            st, r21 = j.conn.execute("SELECT status, ret21 FROM outcomes").fetchone()
            self.assertEqual(st, "open")
            self.assertIsNone(r21)                                  # less than 21 trading days: not graded yet
            j.close()


class TestIdeas(unittest.TestCase):
    def test_same_stock_while_open_is_one_idea(self):
        df = pd.DataFrame({
            "ticker": ["A", "A", "A", "A", "B"],
            "run_date": ["2026-01-05", "2026-01-12", "2026-02-20", "2026-03-30", "2026-01-12"],
            "status": ["closed", "closed", "closed", "open", "open"],
            "exit_date": ["2026-03-01", "2026-03-05", "2026-03-20", None, None]})
        kept = review.ideas(df)
        # 01-12 and 02-20: the first trade was still open (until 03-01); 03-30: it ended and 3 weeks passed
        self.assertEqual(sorted(zip(kept["ticker"], kept["run_date"])),
                         [("A", "2026-01-05"), ("A", "2026-03-30"), ("B", "2026-01-12")])


class TestPostMortem(unittest.TestCase):
    def _row(self, **kw):
        base = {"ticker": "X", "run_date": "2026-03-02", "entry_date": "2026-03-03", "exit_date": "2026-03-20",
                "kind": -1, "ret": -0.09, "days": 12, "spy": 0.01, "mfe_r": 0.3, "prob": 0.3,
                "earnings_date": None, "pros": json.dumps(["סיבה א", "סיבה ב"])}
        base.update(kw)
        return pd.Series(base)

    def test_causes(self):
        pm = review.post_mortem(self._row(spy=-0.06), {})
        self.assertEqual(pm["verdict"], "wrong")
        self.assertEqual(pm["tags"][0], "market")
        pm = review.post_mortem(self._row(earnings_date="2026-03-10"), {})
        self.assertIn("earnings", pm["tags"])
        pm = review.post_mortem(self._row(mfe_r=2.2), {})
        self.assertIn("reversal", pm["tags"])
        pm = review.post_mortem(self._row(), {"hot_month": 1.0})
        self.assertEqual(pm["tags"][0], "stock")
        self.assertIn("entry", pm["tags"])
        pm = review.post_mortem(self._row(kind=1, ret=0.25, mfe_r=3.0), {})
        self.assertEqual(pm["verdict"], "right")
        self.assertEqual(pm["said"], ["סיבה א", "סיבה ב"])


def _seed(j: Journal, n_days=12, per_day=40, bad_key="atr_pct", seed=1):
    """Recommendations where very volatile stocks lose ~6% more than the rest after 21 days."""
    rng = np.random.default_rng(seed)
    start = date(2025, 6, 2)
    pred, out = [], []
    for d in range(n_days):
        rd = (start + timedelta(days=25 * d)).isoformat()
        for i in range(per_day):
            t = f"T{d:02d}{i:02d}"
            hv = i % 3 == 0
            feats = {"atr_pct": 0.09 if hv else 0.02, "rsi14": 0.5, "above_50": 1.0, "mkt_above_200": 1.0}
            ret21 = (-0.06 if hv else 0.0) + rng.normal(0, 0.03)
            pick = int(i < 8)
            pred.append((rd, t, rd, pick, None if pick else "rank", 20.0, 0.4, 0.002, None, None, None, None, 70, 30,
                         "", None, 0, 0.05, json.dumps(feats), json.dumps({"momentum": [float(rng.normal()), 1.0]}),
                         json.dumps(["סיבה"]), None))
            kind = 1 if ret21 > 0.03 else -1
            out.append((rd, t, "closed", rd, 20.0, 19.0, 23.0, rd, 20 * (1 + ret21), kind, ret21, ret21 * 20, 10,
                        0.0, 0.5, ret21, 0.0))
    j.conn.executemany("INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pred)
    j.conn.executemany("INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    j.conn.commit()


class TestLessons(unittest.TestCase):
    def _run(self, meta):
        s = load_settings(root=ROOT)
        panel = _panel({"SPY": np.linspace(400, 420, 400)}, start="2025-05-01")
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "j.sqlite")
            _seed(j)
            rv = review.run(j, panel, {}, date(2026, 6, 1), s, FakeModel(meta))
            state = review.load_state(j)
            j.close()
        return rv, state

    def test_lesson_needs_history_to_agree(self):
        thr = {"atr_pct": 0.05}
        rv, state = self._run({"thresholds": thr})                 # model without history checks yet
        les = {x["key"]: x for x in rv["lessons"]}
        self.assertEqual(les["high_vol"]["status"], "pending")
        self.assertIn("lessons", rv["retrain_request"]["reasons"])
        self.assertFalse(review.active_lessons(state))
        rv, state = self._run({"thresholds": thr, "lesson_tests": {"high_vol": {"passed": True, "sel": {"delta": 0.01}}}})
        self.assertEqual({x["key"]: x for x in rv["lessons"]}["high_vol"]["status"], "adopted")
        self.assertEqual([x["key"] for x in review.active_lessons(state)], ["high_vol"])
        self.assertTrue(any("למדתי" in c["text"] for c in rv["conclusions"]))
        self.assertTrue(any("לקח חדש אומץ" in x["text"] for x in rv["log"]))
        rv, state = self._run({"thresholds": thr, "lesson_tests": {"high_vol": {"passed": False}}})
        self.assertEqual({x["key"]: x for x in rv["lessons"]}["high_vol"]["status"], "rejected")
        self.assertFalse(review.active_lessons(state))
        self.assertTrue(any("מזל רע" in c["text"] for c in rv["conclusions"]))

    def test_scorecard_and_json(self):
        rv, _state = self._run({"thresholds": {"atr_pct": 0.05}})
        self.assertGreater(rv["score"]["n"], 50)
        self.assertGreater(rv["final"]["n"], 50)
        from radar.export import _clean
        json.dumps(_clean(rv), allow_nan=False)


class TestHistoryCheck(unittest.TestCase):
    def test_test_lessons_finds_a_real_pattern(self):
        rng = np.random.default_rng(3)
        rows = []
        for p in range(40):
            for i in range(60):
                score = rng.normal()
                hv = rng.random() < 0.3
                ret = 0.01 * score + (-0.08 if hv else 0.0) + rng.normal(0, 0.02)
                rows.append({"pos": p, "p": score, "ret": ret, "atr_pct": 0.09 if hv else 0.02, "rsi14": 0.5})
        T = pd.DataFrame(rows)
        tests = review.test_lessons(T, list(range(20)), list(range(20, 40)), "p", 10, {"atr_pct": 0.05})
        self.assertTrue(tests["high_vol"]["passed"])
        self.assertGreater(tests["high_vol"]["fin"]["delta"], 0)
        self.assertFalse(tests["overbought"]["passed"])           # never true here: nothing to skip


class TestScanUsesLessons(unittest.TestCase):
    def test_adopted_lesson_skips_stocks(self):
        from radar.pipeline import run_scan

        with tempfile.TemporaryDirectory() as d:
            s = load_settings(root=ROOT)
            s.cfg["general"]["data_dir"] = str(Path(d) / "data")
            s.root = Path(d)
            first = run_scan(s, demo=True)
            self.assertTrue(first["picks"])
            j = Journal(s.data_dir / "journal_demo.sqlite")
            state = {"model_id": None, "thresholds": {"atr_pct": 0.0}, "drift": "none", "bucket_live": {},
                     "lessons": [{"key": "high_vol", "status": "adopted", "text": "מניות תנודתיות מאוד"}]}
            j.conn.execute("INSERT OR REPLACE INTO kv VALUES ('review_state', ?)", (json.dumps(state),))
            j.close()
            res = run_scan(s, demo=True)
            self.assertFalse(res["picks"])                           # every stock is 'very volatile' at threshold 0
            skipped = [r for r in res["results"] if r.get("why_not") == "lesson:high_vol"]
            self.assertTrue(skipped)
            self.assertEqual(skipped[0]["lesson"]["key"], "high_vol")
            self.assertIsNotNone(res["review"])
            n = Journal(s.data_dir / "journal_demo.sqlite").conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            self.assertEqual(n, len(res["results"]))


class TestNeedTrain(unittest.TestCase):
    def test_review_can_ask_for_early_training(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("need_train", ROOT / "ci" / "need_train.py")
        nt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(nt)
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as d:
            nt.DATA = Path(d)
            trained = datetime.now(timezone.utc) - timedelta(days=10)
            (Path(d) / "review.json").write_text(json.dumps({"retrain_request": {
                "date": date.today().isoformat(), "reasons": ["drift"]}}), encoding="utf-8")
            self.assertTrue(nt.review_request(trained))
            self.assertFalse(nt.review_request(datetime.now(timezone.utc) - timedelta(days=3)))  # too fresh
            (Path(d) / "review.json").write_text(json.dumps({"retrain_request": None}), encoding="utf-8")
            self.assertFalse(nt.review_request(trained))


if __name__ == "__main__":
    unittest.main()
