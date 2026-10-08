"""Tests for the historical data, point-in-time features and the probability model.
Run:  python -m unittest discover tests"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.features import (FEATURES, InsiderArrays, earnings_at, earnings_matrix, live_features,  # noqa: E402
                            merge_earnings, tech_features, tech_frame)
from radar.model import ProbModel, Transform, auc, fit_logistic, probability_signal, sigmoid  # noqa: E402
from radar.signals.earnings import earnings_drift_signal  # noqa: E402
from radar.signals.technical import compute_panel_features, eligibility, scores_at  # noqa: E402
from radar.sources import sec  # noqa: E402
from radar.sources.market import YahooMarket, normalize_earnings  # noqa: E402
from radar.train import make_labels  # noqa: E402


def _zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return b.getvalue()


SUB = ("ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDOCUMENT_TYPE\tISSUERCIK\tISSUERNAME\n"
       "A1\t15-MAR-2024\t13-MAR-2024\t4\t320193\tAPPLE\n"
       "A2\t16-MAR-2024\t14-MAR-2024\t4/A\t789\tOTHER\n"
       "A3\t17-MAR-2024\t13-MAR-2024\t3\t320193\tAPPLE\n")
TRANS = ("ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE"
         "\tTRANS_ACQUIRED_DISP_CD\tSHRS_OWND_FOLWNG_TRANS\n"
         "A1\t1\tCommon\t13-MAR-2024\tP\t1000\t10.5\tA\t5000\n"
         "A1\t2\tCommon\t13-MAR-2024\tM\t1000\t10.5\tA\t6000\n"
         "A2\t3\tCommon\t14-MAR-2024\tS\t200\t11\tD\t100\n"
         "A3\t4\tCommon\t14-MAR-2024\tP\t200\t11\tA\t100\n")
OWNER = ("ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
         "A1\t1\tDoe Jane\tDirector,Officer\tChief Executive Officer\n"
         "A2\t2\tBig Fund LP\tTenPercentOwner\t\n")


class TestHistoricalData(unittest.TestCase):
    def test_form345_dataset(self):
        content = _zip({"2024q1/SUBMISSION.tsv": SUB, "2024q1/NONDERIV_TRANS.tsv": TRANS,
                        "2024q1/REPORTINGOWNER.tsv": OWNER})
        df = sec.parse_form345_zip(content)
        self.assertEqual(len(df), 2)  # only P/S from Form 4 and 4/A
        buy = df[df["code"] == "P"].iloc[0]
        self.assertEqual(int(buy["cik"]), 320193)
        self.assertEqual(buy["filing_date"], date(2024, 3, 15))
        self.assertEqual(buy["date"], date(2024, 3, 13))
        self.assertEqual(buy["shares_after"], 5000)
        self.assertTrue(buy["is_officer"] and buy["top_exec"] and not buy["is_ten_pct"])
        sell = df[df["code"] == "S"].iloc[0]
        self.assertTrue(sell["is_ten_pct"])
        self.assertEqual(len(sec.parse_form345_zip(content, {320193})), 1)

    def test_quarters_back(self):
        self.assertEqual(sec.quarters_back(3, date(2026, 10, 8)), ["2026q3", "2026q2", "2026q1"])
        self.assertEqual(sec.quarters_back(2, date(2026, 2, 1)), ["2025q4", "2025q3"])

    def test_insider_history_tries_url_patterns(self):
        content = _zip({"SUBMISSION.tsv": SUB, "NONDERIV_TRANS.tsv": TRANS, "REPORTINGOWNER.tsv": OWNER})
        seen = []

        class FakeHttp:
            def get_bytes(self, url, cache_name=None):
                seen.append(url)
                return content if "structureddata/data/insider" in url else None

        df = sec.insider_history(FakeHttp(), 1)
        self.assertEqual(len(df), 2 * 5)  # 5 quarters, same fake file each time
        self.assertTrue(any("datastandardsinnovation" in u for u in seen))

    def test_earnings_dates_from_8k(self):
        recent = {"form": ["8-K", "4", "8-K", "10-Q", "8-K"], "items": ["2.02,9.01", "", "5.02", "", "2.02"],
                  "filingDate": ["2026-07-30", "2026-07-01", "2026-06-01", "2026-05-01", "2026-04-30"]}
        older = {"form": ["8-K"], "items": ["2.02"], "filingDate": ["2025-01-30"]}

        class FakeHttp:
            def get(self, url, as_json=False, ttl=None):
                if url.endswith("CIK0000000042.json"):
                    return {"filings": {"recent": recent, "files": [
                        {"name": "CIK0000000042-submissions-001.json", "filingTo": "2025-12-31"}]}}
                if url.endswith("submissions-001.json"):
                    return older
                return None

        d = sec.earnings_filing_dates(FakeHttp(), 42, date(2024, 1, 1))
        self.assertEqual(d, [date(2025, 1, 30), date(2026, 4, 30), date(2026, 7, 30)])
        self.assertIsNone(sec.earnings_filing_dates(FakeHttp(), 7, date(2024, 1, 1)))

    def test_yahoo_earnings(self):
        idx = pd.DatetimeIndex(pd.to_datetime(["2099-01-30 16:00", "2026-07-30 16:05", "2026-04-30 08:00"])
                               .tz_localize("America/New_York"), name="Earnings Date")
        df = pd.DataFrame({"EPS Estimate": [1.2, 1.0, 0.8], "Reported EPS": [np.nan, 1.1, 0.7],
                           "Surprise(%)": [np.nan, 10.0, np.nan]}, index=idx)
        out = normalize_earnings(df)
        self.assertEqual(list(out["date"]), [date(2026, 4, 30), date(2026, 7, 30)])
        self.assertAlmostEqual(out["surprise"].iloc[1], 0.10)
        self.assertAlmostEqual(out["surprise"].iloc[0], -0.125)  # computed from estimate vs reported

        class T:
            def __init__(self, s):
                pass

            def get_earnings_dates(self, limit=12):
                return df

        m = YahooMarket(Path(tempfile.gettempdir()), yf_module=types.SimpleNamespace(Ticker=T, download=None))
        self.assertEqual(len(m.earnings_history("X")), 2)

    def test_long_period_uses_start_date(self):
        calls = []

        def dl(tickers, **kw):
            calls.append(kw)
            idx = pd.bdate_range("2020-01-01", periods=5)
            cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], tickers])
            return pd.DataFrame(1.0, index=idx, columns=cols)

        m = YahooMarket(Path(tempfile.gettempdir()), yf_module=types.SimpleNamespace(Ticker=None, download=dl))
        m.download_prices(["AAA"], period="6y", use_cache=False)
        m.download_prices(["AAA"], period="2y", use_cache=False)
        self.assertIn("start", calls[0])
        self.assertNotIn("period", calls[0])
        self.assertEqual(calls[1]["period"], "2y")


class TestFeatures(unittest.TestCase):
    def test_earnings_point_in_time(self):
        idx = pd.bdate_range("2024-01-01", periods=200)
        close = pd.Series(np.linspace(10, 20, 200), index=idx)
        k = idx.searchsorted(pd.Timestamp("2024-03-11"))
        close.iloc[k + 1] = close.iloc[k - 1] * 1.2
        spy = pd.Series(100.0, index=idx)
        ev = merge_earnings([date(2024, 3, 11)], pd.DataFrame({"date": [date(2024, 3, 8)], "surprise": [0.15]}))
        self.assertEqual(len(ev), 1)
        self.assertAlmostEqual(ev["surprise"].iloc[0], 0.15)
        m = earnings_matrix(ev, close, spy, [k, k + 1, k + 1 + 63, k + 2 + 63])
        self.assertEqual(list(m["earn_recent"]), [0, 1, 1, 0])  # unknown before the reaction day closes
        self.assertAlmostEqual(m["earn_reaction"][1], 0.2, places=6)
        self.assertEqual(earnings_at(ev, close, spy, k + 5)["earn_surprise"], 0.15)
        self.assertTrue(np.isnan(earnings_at(None, close, spy, k + 5)["earn_recent"]))
        self.assertEqual(earnings_at(ev.iloc[:0], close, spy, k + 5)["earn_recent"], 0.0)
        self.assertIsNone(merge_earnings(None, None))

    def test_insider_windows(self):
        rows = [{"filing_date": date(2026, 3, 2), "date": date(2026, 2, 27), "code": "P", "shares": 10000, "price": 30,
                 "ad": "A", "shares_after": 20000, "owner": "CEO A", "is_officer": True, "top_exec": True},
                {"filing_date": date(2026, 3, 5), "code": "P", "shares": 100, "price": 30, "ad": "A",
                 "owner": "Fund", "is_ten_pct": True},
                {"filing_date": date(2026, 3, 6), "code": "S", "shares": 1e6, "price": 30, "ad": "D", "owner": "X",
                 "planned": True}]
        a = InsiderArrays(rows)
        self.assertEqual(a.at(date(2026, 3, 1).toordinal())["ins_buy"], 0.0)  # not filed yet
        f = a.at(date(2026, 3, 10).toordinal())
        self.assertEqual(f["ins_buy"], 1.0)
        self.assertEqual(f["ins_n_buyers"], 1.0)  # the 10% fund is not an insider buyer
        self.assertEqual(f["ins_top_exec"], 1.0)
        self.assertAlmostEqual(f["ins_hold_incr"], 1.0)  # 10k on top of 10k
        self.assertEqual(f["ins_net_sell"], 0.0)  # planned (10b5-1) sales are ignored
        self.assertEqual(a.at(date(2026, 9, 1).toordinal())["ins_buy"], 0.0)  # older than 90 days
        self.assertEqual(InsiderArrays([]).at(1)["ins_buy"], 0.0)

    def test_training_and_live_features_match(self):
        """The same stock on the same day must get identical features in training and in the daily scan."""
        from radar.sources.demo import DemoSources

        src = DemoSources(n=60, years=3)
        panel = src.prices(src.tickers + ["SPY"], "3y")
        f = compute_panel_features(panel)
        t = panel["Close"].index[-1]
        elig = eligibility(f, {"min_price": 0, "min_dollar_volume": 0, "min_history_days": 0}).loc[t]
        sc = scores_at(f, t, elig, {})
        tf = tech_frame(f, t)
        tk = sc.index[3]
        live_tech = tech_features(sc.loc[tk].to_dict())
        for k, v in live_tech.items():
            self.assertAlmostEqual(v, float(tf.loc[tk, k]), places=9, msg=k)
        ev = src.earnings_events(tk, None, since=date(2000, 1, 1))
        spy = panel["Close"]["SPY"]
        close = panel["Close"][tk].dropna()
        live = live_features(sc.loc[tk].to_dict(), [], ev, close, spy, t.date())
        em = earnings_matrix(ev, close, spy.reindex(close.index).ffill(), [len(close) - 1])
        for k in em:
            self.assertAlmostEqual(live[k], float(em[k][0]), places=9, msg=k)
        self.assertEqual(set(live), set(FEATURES))


class TestModel(unittest.TestCase):
    def test_logistic_recovers_signal(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(20000, 3))
        y = (rng.random(20000) < sigmoid(-2 + 1.2 * X[:, 0] - 0.8 * X[:, 1])).astype(float)
        w = fit_logistic(np.column_stack([np.ones(len(X)), X]), y, lam=1.0)
        self.assertAlmostEqual(w[1], 1.2, delta=0.1)
        self.assertAlmostEqual(w[2], -0.8, delta=0.1)
        self.assertLess(abs(w[3]), 0.1)
        self.assertAlmostEqual(auc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]), 0.75)
        self.assertEqual(auc([1, 0], [0.5, 0.5]), 0.5)

    def test_transform_handles_missing(self):
        raw = np.array([[1.0, np.nan], [2.0, 1.0], [3.0, 0.0], [100.0, 1.0]])
        tf = Transform.fit(raw, ["rsi14", "ins_buy"])
        z = tf(raw)
        self.assertEqual(z.shape[1], 1 + 2 + 1)  # intercept, 2 features, 1 square (rsi14)
        self.assertFalse(np.isnan(z).any())
        self.assertEqual(z[0, 2], 0.0)  # missing -> average

    def test_labels(self):
        # 3 stocks, entry at next open = 10, ATR 1 -> stop 7.5, target 13
        T = 10
        O = np.full((T, 3), 10.0)
        H = np.full((T, 3), 10.5)
        L = np.full((T, 3), 9.5)
        atr = np.ones((T, 3))
        H[3, 0] = 13.5                  # stock 0: target first
        L[2, 1] = 7.0
        H[4, 1] = 14.0                  # stock 1: stop first
        H[3, 2], L[3, 2] = 13.5, 7.0    # stock 2: both the same day -> counted as a loss
        y = make_labels(O, H, L, atr, [0], 5, 0.30, 2.5)[0]
        self.assertEqual(list(y), [1.0, 0.0, 0.0])
        atr[0, 0] = np.nan
        self.assertTrue(np.isnan(make_labels(O, H, L, atr, [0], 5, 0.30, 2.5)[0][0]))

    def test_signals(self):
        s = earnings_drift_signal({"earn_recent": 1.0, "earn_reaction": 0.09, "earn_surprise": 0.2, "earn_age": 0.1})
        self.assertGreater(s.score, 0.5)
        self.assertTrue(any("דוח" in r for r in s.reasons))
        self.assertEqual(earnings_drift_signal({"earn_recent": float("nan")}).conf, 0.0)
        neg = earnings_drift_signal({"earn_recent": 1.0, "earn_reaction": -0.1, "earn_surprise": float("nan"),
                                     "earn_age": 0.0})
        self.assertLess(neg.score, -0.5)
        self.assertEqual(probability_signal(None, None).conf, 0.0)


def _settings(d: str):
    from radar.settings import load_settings

    s = load_settings(root=ROOT)
    s.cfg["general"]["data_dir"] = str(Path(d) / "data")
    s.cfg["general"]["reports_dir"] = str(Path(d) / "reports")
    s.root = Path(d)
    return s


class TestTrainEndToEnd(unittest.TestCase):
    def test_demo_train_then_scan(self):
        from radar.export import _clean, scan_json
        from radar.pipeline import run_scan
        from radar.train import model_age_days, train_model

        with tempfile.TemporaryDirectory() as d:
            s = _settings(d)
            s.cfg["train"]["years"] = 4
            path = Path(d) / "model.json"
            meta = train_model(s, demo=True, out_path=path)
            v = meta["validation"]
            self.assertGreater(meta["n_rows"], 5000)
            self.assertGreater(v["auc"], 0.55)               # the demo market has real (synthetic) structure
            self.assertGreater(v["top_rate"], v["base"])
            self.assertEqual(len(v["deciles"]), 10)
            self.assertTrue(meta["what_worked"])
            self.assertTrue(meta["coverage"]["insider"])
            self.assertLess(model_age_days(path), 1)
            m = ProbModel.load(path)
            self.assertGreater(m.quality, 0)
            res = run_scan(s, demo=True, model_path=path)
            probs = [r["prob"] for r in res["results"]]
            self.assertTrue(all(p is not None and 0 < p < 1 for p in probs))
            self.assertIn("model", res["results"][0]["signals"])
            self.assertIn("earnings_drift", res["results"][0]["signals"])
            j = scan_json(res)
            self.assertIsNotNone(j["results"][0]["prob"])
            self.assertIsNotNone(j["model"]["auc"])
            json.dumps(_clean(j), allow_nan=False)
            # a demo model is never used for real data
            s2 = _settings(d)
            from radar.pipeline import load_model
            self.assertIsNone(load_model(s2, False, path))
            # without a model, the model weight is switched off and the scan still works
            res2 = run_scan(s, demo=True, model_path=Path(d) / "missing.json")
            self.assertTrue(all(r["prob"] is None for r in res2["results"]))
            self.assertEqual(res2["weights"]["model"], 0.0)


class TestCloudScripts(unittest.TestCase):
    def _need(self, root: Path) -> str:
        script = (ROOT / "ci" / "need_train.py").read_text(encoding="utf-8")
        (root / "ci").mkdir(parents=True, exist_ok=True)
        (root / "ci" / "need_train.py").write_text(script, encoding="utf-8")
        return subprocess.run([sys.executable, str(root / "ci" / "need_train.py")], capture_output=True,
                              text=True, check=True).stdout.strip()

    def test_need_train(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data = root / "docs" / "data"
            data.mkdir(parents=True)
            (root / "config.yaml").write_text("train:\n  enabled: true\n  retrain_days: 30\nalerts:\n  x: 1\n",
                                              encoding="utf-8")
            self.assertEqual(self._need(root), "yes")                  # no model yet
            (data / "model_attempt.txt").write_text(date.today().isoformat(), encoding="utf-8")
            self.assertEqual(self._need(root), "no")                   # tried today: wait
            (data / "model_attempt.txt").unlink()
            old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
            (data / "model.json").write_text(json.dumps({"trained": old}), encoding="utf-8")
            self.assertEqual(self._need(root), "yes")                  # model too old
            (data / "model.json").write_text(json.dumps({"trained": datetime.now(timezone.utc).isoformat()}),
                                             encoding="utf-8")
            self.assertEqual(self._need(root), "no")                   # fresh model
            (data / "model.json").unlink()
            (root / "config.yaml").write_text("train:\n  enabled: false\n", encoding="utf-8")
            self.assertEqual(self._need(root), "no")                   # switched off

    def test_shell_scripts_know_train(self):
        run = (ROOT / "ci" / "run.sh").read_text(encoding="utf-8")
        save = (ROOT / "ci" / "save.sh").read_text(encoding="utf-8")
        self.assertIn("train)", run)
        self.assertIn("mode=train", save)
        self.assertIn(".radar_rc", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
