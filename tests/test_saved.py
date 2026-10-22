"""Tests for the stocks starred in the app (ci/saved.py and the scan reading saved.json).
Run:  python -m unittest discover tests"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ci"))

import run  # noqa: E402
import saved  # noqa: E402
from radar.settings import load_settings  # noqa: E402


class TestSaved(unittest.TestCase):
    def test_writes_the_list(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "saved.json"
            saved.main(["prch", "CBRL", "PRCH", "bad ticker!", "BRK-B"], out)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["tickers"], ["PRCH", "CBRL", "BRK-B"])
            self.assertTrue(data["updated"])
            saved.main([], out)                                   # the list was emptied in the app
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["tickers"], [])

    def test_keeps_a_snapshot_from_when_it_was_saved(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "saved.json"
            latest = {"date": "2026-10-21", "spy_closes": [600.0, None, 610.5],
                      "results": [{"ticker": "PRCH", "name": "Porch", "price": 18.18, "upside": 75, "risk": 21,
                                   "pick": True, "why_not": None, "pros": ["a", "b", "c", "d"], "cons": ["x"],
                                   "tg_n": 0, "plan": {"stop": 16.15, "target": 24.26}}]}
            (Path(d) / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
            saved.main(["PRCH", "NVDA"], out)
            snap = json.loads(out.read_text(encoding="utf-8"))["items"]["PRCH"]["snap"]
            self.assertEqual((snap["price"], snap["upside"], snap["stop"], snap["spy"]), (18.18, 75, 16.15, 610.5))
            self.assertEqual(snap["pros"], ["a", "b", "c"])
            first = json.loads(out.read_text(encoding="utf-8"))["items"]["PRCH"]["since"]
            latest["results"][0]["price"] = 30.0                  # a later scan
            (Path(d) / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
            saved.main(["PRCH", "NVDA", "CBRL"], out)
            items = json.loads(out.read_text(encoding="utf-8"))["items"]
            self.assertEqual(items["PRCH"]["snap"]["price"], 18.18)   # the first snapshot is kept
            self.assertEqual(items["PRCH"]["since"], first)
            self.assertIsNone(items["NVDA"]["snap"])                  # nothing known about it yet

    def test_scan_analyses_saved_stocks(self):
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(root=ROOT)
            s.cfg["general"]["data_dir"] = str(Path(d) / "data")
            s.cfg["general"]["reports_dir"] = str(Path(d) / "reports")
            s.root = Path(d)
            out = Path(d) / "docs"
            saved.main(["DMO005", "DMO006"], out / "demo" / "saved.json")
            args = Namespace(demo=True, demo_history=0, export=str(out), no_send=True, open=False)
            run.cmd_scan(s, args)
            res = json.loads((out / "demo" / "latest.json").read_text(encoding="utf-8"))["results"]
            got = {r["ticker"]: r for r in res}
            for t in ("DMO005", "DMO006"):
                self.assertIn(t, got)                              # analysed although not among the top candidates
                if got[t]["why_not"] == "filters":                 # kept in only because it was saved: never a pick
                    self.assertFalse(got[t]["pick"])


if __name__ == "__main__":
    unittest.main()
