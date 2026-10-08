"""Tests for parsers, signals, journal and an end-to-end demo run.  Run:  python -m unittest discover tests"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar import journal as jmod  # noqa: E402
from radar.scoring import composite, position_plan  # noqa: E402
from radar.signals.analysts import (firm_accuracy, firm_calls_from_history, normalize_upgrades,  # noqa: E402
                                    ratings_signal, revisions_signal, target_signal)
from radar.signals.context import compute_regime, geopolitics_signal, gov_signal  # noqa: E402
from radar.signals.flows import options_signal, short_squeeze_signal  # noqa: E402
from radar.signals.insider import insider_signal, yahoo_insider_rows  # noqa: E402
from radar.signals.news import analyze_news, catalysts_signal, lexicon_score  # noqa: E402
from radar.sources import countries, fred, gdelt, sec, usaspending  # noqa: E402
from radar.sources.market import YahooMarket, normalize_news, parse_calendar, to_panel  # noqa: E402
from radar.util import SignalResult  # noqa: E402

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion><documentType>4</documentType><aff10b5One>0</aff10b5One>
  <issuer><issuerCik>0001234567</issuerCik><issuerName>Example Corp</issuerName>
    <issuerTradingSymbol>exmp</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0009876543</rptOwnerCik><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>false</isDirector><isOfficer>true</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner><officerTitle>Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-09-15</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>10,000</value></transactionShares>
        <transactionPricePerShare><value>12.50</value><footnoteId id="F1"/></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-16</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>13</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <footnotes><footnote id="F1">Weighted average price.</footnote></footnotes>
</ownershipDocument>"""

DAILY_INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-K        SOME CO                                                       1111111     20260915    edgar/data/1111111/0001111111-26-000001.txt
4           Doe Jane                                                      9876543     20260915    edgar/data/9876543/0001234567-26-000010.txt
4           Example Corp                                                  1234567     20260915    edgar/data/1234567/0001234567-26-000010.txt
4/A         Other Person                                                  5555555     20260915    edgar/data/5555555/0005555555-26-000002.txt
"""


class TestSEC(unittest.TestCase):
    def test_parse_form4(self):
        f = sec.parse_form4(FORM4)
        self.assertEqual(f["symbol"], "EXMP")
        self.assertEqual(f["issuer_cik"], 1234567)
        self.assertTrue(f["is_officer"])
        self.assertFalse(f["is_director"])
        self.assertFalse(f["planned"])
        self.assertEqual(len(f["transactions"]), 2)
        tx = f["transactions"][0]
        self.assertEqual((tx["code"], tx["shares"], tx["price"], tx["ad"]), ("P", 10000.0, 12.5, "A"))
        self.assertEqual(tx["date"], date(2026, 9, 15))

    def test_parse_form4_embedded_in_submission(self):
        txt = "<SEC-DOCUMENT>\n<DOCUMENT>\n<TYPE>4\n<TEXT>\n<XML>\n" + FORM4 + "\n</XML>\n</TEXT>\n</DOCUMENT>"
        self.assertEqual(sec.parse_form4(txt)["symbol"], "EXMP")
        self.assertIsNone(sec.parse_form4("garbage"))

    def test_planned_flag_from_footnote(self):
        f = sec.parse_form4(FORM4.replace("Weighted average price.", "Made under a Rule 10b5-1 trading plan."))
        self.assertTrue(f["planned"])

    def test_daily_index(self):
        rows = sec.parse_daily_form_index(DAILY_INDEX)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["accession"], "0001234567-26-000010")
        self.assertEqual(rows[2]["form"], "4/A")

    def test_tickers(self):
        data = {"fields": ["cik", "name", "ticker", "exchange"],
                "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [1067983, "Berkshire", "BRK-B", "NYSE"],
                         [1, "Pref", "BAC-PB", "NYSE"], [2, "Warrant", "ABCDW", "Nasdaq"], [3, "OTC co", "OTCX", "OTC"],
                         [4, "No exch", "NOEX", None]]}
        out = sec.parse_tickers_exchange(data, ["Nasdaq", "NYSE"])
        self.assertEqual([r["ticker"] for r in out], ["AAPL", "BRK-B"])

    def test_flatten(self):
        rows = sec.flatten_filings([sec.parse_form4(FORM4)])
        self.assertTrue(rows[0]["top_exec"])
        self.assertEqual(rows[0]["owner"], "Doe Jane")


class TestOtherSources(unittest.TestCase):
    def test_gdelt(self):
        days = pd.date_range("2026-07-01", periods=90)
        pts = [{"date": d.strftime("%Y%m%dT%H%M%SZ"), "value": 1.0 if i < 83 else 2.0} for i, d in enumerate(days)]
        series = gdelt.daily_series({"timeline": [{"series": "Volume Intensity", "data": pts}]})
        res = gdelt.intensity_from_series(series)
        self.assertAlmostEqual(res["ratio"], 2.0)
        self.assertEqual(res["escalation"], 1.0)
        self.assertEqual(gdelt.daily_series({"bad": 1}), [])
        self.assertIn("defense", gdelt.match_groups("Aerospace & Defense", ""))
        self.assertIn("cyber", gdelt.match_groups("Software - Infrastructure", "A leading cybersecurity platform"))

    def test_fred(self):
        s = fred.parse_fred_csv("observation_date,DGS10\n2026-09-01,4.2\n2026-09-02,.\n2026-09-03,4.1\n", "DGS10")
        self.assertEqual(len(s), 2)
        self.assertEqual(float(s.iloc[-1]), 4.1)
        s2 = fred.parse_fred_csv("DATE,VIXCLS\n2026-09-01,15\n", "VIXCLS")
        self.assertEqual(float(s2.iloc[0]), 15)
        j = fred.parse_fred_json({"observations": [{"date": "2026-01-01", "value": "3.9"}, {"date": "2026-02-01", "value": "."}]}, "UNRATE")
        self.assertEqual(len(j), 1)

    def test_countries(self):
        imf = {"values": {"NGDP_RPCH": {"ISR": {"2025": 3.2, "2026": "3.6"}}}}
        self.assertEqual(countries.parse_imf(imf, "NGDP_RPCH", "ISR"), {2025: 3.2, 2026: 3.6})
        wb = [{"page": 1}, [{"date": "2024", "value": 1.5}, {"date": "2023", "value": None}]]
        self.assertEqual(countries.parse_worldbank(wb), {2024: 1.5})
        self.assertEqual(countries.iso3_for("Israel"), "ISR")
        self.assertIsNone(countries.iso3_for("Atlantis"))

    def test_usaspending_names(self):
        self.assertTrue(usaspending.names_match("LOCKHEED MARTIN CORPORATION", "Lockheed Martin Corp."))
        self.assertTrue(usaspending.names_match("KRATOS DEFENSE & SECURITY SOLUTIONS, INC.", "Kratos Defense & Security Solutions"))
        self.assertFalse(usaspending.names_match("APPLIED SIGNAL INC", "Apple Inc."))


class FakeTicker:
    def __init__(self, sym):
        self.sym = sym
        self.info = {"longName": "Fake Co", "marketCap": 2e9, "sector": "Technology", "industry": "Semiconductors",
                     "shortPercentOfFloat": 0.2, "shortRatio": 5, "country": "Israel"}
        self.news = [{"id": "x", "content": {"title": "Fake Co raises guidance", "summary": "s",
                                             "pubDate": "2026-10-06T12:00:00Z", "provider": {"displayName": "Wire"},
                                             "canonicalUrl": {"url": "https://example.com/a"}}},
                     {"title": "Old format headline beats estimates", "publisher": "P", "link": "https://e.com",
                      "providerPublishTime": 1790000000}]
        self.calendar = {"Earnings Date": [date.today() + timedelta(days=10)]}
        self.recommendations = pd.DataFrame({"period": ["0m", "-1m"], "strongBuy": [5, 3], "buy": [5, 5],
                                             "hold": [2, 4], "sell": [0, 0], "strongSell": [0, 0]})
        self.upgrades_downgrades = pd.DataFrame(
            {"Firm": ["A"], "ToGrade": ["Buy"], "FromGrade": ["Hold"], "Action": ["up"]},
            index=pd.DatetimeIndex([pd.Timestamp(date.today() - timedelta(days=3))], name="GradeDate"))
        self.eps_trend = pd.DataFrame({"current": [1.1], "7daysAgo": [1.05], "30daysAgo": [1.0], "60daysAgo": [0.98],
                                       "90daysAgo": [0.95]}, index=["+1y"])
        self.eps_revisions = pd.DataFrame({"upLast7days": [1], "upLast30days": [5], "downLast30days": [1],
                                           "downLast7Days": [0]}, index=["+1y"])
        self.analyst_price_targets = {"mean": 15.0, "high": 25.0}
        self.options = ("2026-10-17", "2026-10-24")

    @property
    def insider_transactions(self):
        raise RuntimeError("not available")

    def option_chain(self, exp):
        calls = pd.DataFrame({"strike": [9, 10, 12], "volume": [100, 500, 900], "openInterest": [1000, 1000, 500],
                              "impliedVolatility": [0.6, 0.55, 0.7]})
        puts = pd.DataFrame({"strike": [9, 10], "volume": [50, np.nan], "openInterest": [800, 400]})
        return types.SimpleNamespace(calls=calls, puts=puts)


def fake_download(tickers, **kw):
    idx = pd.bdate_range("2025-01-01", periods=300)
    cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], tickers])
    data = np.abs(np.random.default_rng(0).normal(10, 1, (len(idx), len(cols))))
    return pd.DataFrame(data, index=idx, columns=cols)


class TestMarketAdapter(unittest.TestCase):
    def setUp(self):
        self.yf = types.SimpleNamespace(Ticker=FakeTicker, download=fake_download)
        self.tmp = tempfile.TemporaryDirectory()
        self.m = YahooMarket(Path(self.tmp.name), yf_module=self.yf)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prices(self):
        p = self.m.download_prices(["AAA", "BBB", "SPY"], use_cache=False)
        self.assertEqual(set(p["Close"].columns), {"AAA", "BBB", "SPY"})
        single = to_panel(fake_download(["AAA"]).droplevel(1, axis=1), ["AAA"])
        self.assertEqual(list(single["Close"].columns), ["AAA"])

    def test_details(self):
        d = self.m.details("FAKE", price=10.0)
        self.assertEqual(d["info"]["longName"], "Fake Co")
        self.assertEqual(len(d["news"]), 2)
        self.assertEqual(d["news"][0]["source"], "Wire")
        self.assertIsNotNone(d["news"][0]["published"].tzinfo)
        self.assertEqual(d["earnings_date"], date.today() + timedelta(days=10))
        self.assertIsNone(d["insider_yahoo"])
        o = d["options"]
        self.assertEqual(o["call_vol"], 3000)
        self.assertEqual(o["put_vol"], 100)
        self.assertEqual(o["otm_call_vol"], 1800)

    def test_calendar_formats(self):
        df = pd.DataFrame({0: [pd.Timestamp(date.today() + timedelta(days=5))]}, index=["Earnings Date"])
        self.assertEqual(parse_calendar(df), date.today() + timedelta(days=5))
        self.assertIsNone(parse_calendar(None))
        self.assertEqual(normalize_news([{"nothing": 1}]), [])


class TestSignals(unittest.TestCase):
    today = date(2026, 10, 7)

    def test_insider(self):
        rows = sec.flatten_filings([sec.parse_form4(FORM4)])
        s = insider_signal(rows, 5e8, 90, self.today)
        self.assertGreater(s.score, 0.2)
        self.assertIn("CEO", s.reasons[0]) if "CEO" in s.reasons[0] else self.assertIn("Chief", s.reasons[0])
        empty = insider_signal([], 5e8, 90, self.today)
        self.assertEqual(empty.score, 0)
        yrows = yahoo_insider_rows(pd.DataFrame({"Shares": [1000], "Value": [20000], "Text": ["Purchase at price 20 per share."],
                                                 "Insider": ["X"], "Position": ["Chief Financial Officer"],
                                                 "Start Date": ["2026-10-01"]}))
        self.assertEqual(yrows[0]["code"], "P")
        self.assertTrue(yrows[0]["top_exec"])

    def test_analysts(self):
        t = FakeTicker("X")
        r = revisions_signal(t.eps_trend, t.eps_revisions)
        self.assertGreater(r.score, 0.5)
        self.assertTrue(any("10%" in x for x in r.reasons))
        ud = normalize_upgrades(t.upgrades_downgrades)
        self.assertEqual(ud.iloc[0]["action"], "up")
        rs = ratings_signal(ud, t.recommendations, {"A": {"acc": 0.7, "n": 20}}, date.today())
        self.assertGreater(rs.score, 0)
        ts = target_signal(t.analyst_price_targets, {"numberOfAnalystOpinions": 10}, 10.0)
        self.assertAlmostEqual(ts.data["upside"], 0.5)
        self.assertEqual(normalize_upgrades(None).shape[0], 0)

    def test_firm_accuracy(self):
        idx = pd.bdate_range("2025-01-01", periods=200)
        close = pd.Series(np.linspace(10, 20, 200), index=idx)
        spy = pd.Series(np.linspace(100, 110, 200), index=idx)
        ud = normalize_upgrades(pd.DataFrame({"Firm": ["Good", "Bad"], "ToGrade": ["Buy", "Sell"],
                                              "FromGrade": ["Hold", "Hold"], "Action": ["up", "down"]},
                                             index=pd.DatetimeIndex([idx[10], idx[20]], name="GradeDate")))
        calls = firm_calls_from_history("X", ud, close, spy, 60)
        acc = firm_accuracy(calls)
        self.assertGreater(acc["Good"]["acc"], 0.5)
        self.assertLess(acc["Bad"]["acc"], 0.5)

    def test_flows(self):
        tech = {"close": 12, "sma50": 10, "ret_21": 0.1}
        s = short_squeeze_signal({"shortPercentOfFloat": 0.3, "shortRatio": 8}, tech)
        self.assertGreater(s.score, 0.5)
        self.assertEqual(s.risk_add, 8)
        down = short_squeeze_signal({"shortPercentOfFloat": 30, "shortRatio": 8}, {"close": 8, "sma50": 10, "ret_21": -0.1})
        self.assertLess(down.score, 0)
        o = options_signal({"call_vol": 5000, "put_vol": 500, "call_oi": 4000, "put_oi": 2000, "otm_call_vol": 3000}, tech)
        self.assertGreater(o.score, 0.3)
        self.assertEqual(options_signal(None, tech).conf, 0)

    def test_news(self):
        self.assertGreater(lexicon_score("Acme raises guidance")[0], 0)
        self.assertLess(lexicon_score("Acme announces $20 million registered direct offering")[0], 0)
        self.assertEqual(lexicon_score("Steps to grow")[1], [])
        now = datetime(2026, 10, 7, tzinfo=timezone.utc)
        news = [{"title": "Acme wins contract from U.S. Navy", "published": now - timedelta(days=1), "source": "W"},
                {"title": "Acme sets PDUFA date", "published": now - timedelta(days=20), "source": "W"}]
        sig, flags = analyze_news(news, 7, now)
        self.assertGreater(sig.score, 0)
        self.assertIn("pdufa", flags)
        cat = catalysts_signal(date(2026, 10, 20), 0.5, flags, date(2026, 10, 7), 5)
        self.assertGreater(cat.score, 0.3)
        self.assertGreater(cat.risk_add, 0)

    def test_context(self):
        themes = {"me": {"escalation": 1.0, "ratio": 1.8, "he": "המזרח התיכון", "tilts": {"defense": 0.6, "airlines": -0.6}}}
        self.assertGreater(geopolitics_signal(themes, "Aerospace & Defense", "").score, 0)
        self.assertLess(geopolitics_signal(themes, "Airlines", "").score, 0)
        self.assertEqual(geopolitics_signal(themes, "Banks - Regional", "").conf, 0)
        g = gov_signal({"total": 5e7, "count": 3, "agencies": ["DoD"]}, 1e9, 120)
        self.assertAlmostEqual(g.score, 1.0)
        idx = pd.bdate_range("2025-01-01", periods=300)
        spy = pd.Series(np.linspace(100, 130, 300), index=idx)
        reg = compute_regime({"VIXCLS": pd.Series([14.0] * 30, index=idx[:30])}, spy)
        self.assertGreater(reg["score"], 0.25)

    def test_composite_and_plan(self):
        sigs = {"a": SignalResult("a", 1.0, 1.0), "b": SignalResult("b", -1.0, 0.0)}
        up, cov = composite(sigs, {"a": 1.0, "b": 1.0})
        self.assertEqual(up, 100.0)
        self.assertEqual(cov, 0.5)
        self.assertEqual(composite({}, {"a": 1})[0], 50.0)
        plan = position_plan(50.0, 2.0, {"account_size": 50000, "risk_per_trade_pct": 1, "atr_stop_multiple": 2.5,
                                          "max_position_pct": 10})
        self.assertEqual(plan["stop"], 45.0)
        self.assertEqual(plan["shares"], 100)  # capped by 10% of account (5000/50)


class TestJournal(unittest.TestCase):
    def test_evaluate_learn_paper(self):
        with tempfile.TemporaryDirectory() as d:
            j = jmod.Journal(Path(d) / "j.sqlite")
            idx = pd.bdate_range(end=pd.Timestamp(date.today()), periods=120)
            up = np.linspace(10, 20, 120)
            panel = {"Open": pd.DataFrame({"UP": up, "DN": up[::-1], "SPY": np.full(120, 100.0)}, index=idx)}
            panel["Close"] = panel["Open"].copy()
            panel["High"] = panel["Open"] * 1.01
            panel["Low"] = panel["Open"] * 0.99
            run_day = idx[10].date()
            res = []
            for t, sc in (("UP", 0.8), ("DN", -0.8)):
                res.append({"ticker": t, "price": float(panel["Close"][t].iloc[10]), "upside": 50 + sc * 50, "risk": 10,
                            "coverage": 1, "pick": sc > 0, "signals": {"momentum": SignalResult("momentum", sc, 1)},
                            "why": {"pros": [], "cons": []}})
            j.record_run(run_day, {"score": 0.1, "label": "x"}, res, 1)
            self.assertEqual(j.evaluate(panel, date.today()), 4)  # 2 tickers x 2 horizons
            df = j.evaluated_frame(30)
            self.assertGreater(df.set_index("ticker").loc["UP", "excess"], 0)
            self.assertEqual(j.evaluate(panel, date.today()), 0)  # idempotent
            # paper trading: a position that falls through its stop must close
            j.conn.execute("INSERT INTO paper (ticker, entry_date, entry_price, initial_stop, stop, atr, shares, status, last_price) "
                           "VALUES ('DN', ?, ?, ?, ?, 0.5, 10, 'open', ?)",
                           (run_day.isoformat(), float(panel["Close"]["DN"].iloc[10]),
                            float(panel["Close"]["DN"].iloc[10]) - 1, float(panel["Close"]["DN"].iloc[10]) - 1,
                            float(panel["Close"]["DN"].iloc[10])))
            j.paper_update(panel, date.today(), {"trailing_stop": True, "max_hold_days": 90}, 2.5)
            summ = j.paper_summary(panel["Close"]["SPY"])
            self.assertEqual(summ["closed"], 1)
            self.assertLess(summ["realized"], 0)
            j.close()


class TestEndToEnd(unittest.TestCase):
    def test_demo_scan_report_backtest(self):
        from radar.backtest import run_backtest
        from radar.pipeline import run_scan
        from radar.report import alert_text, write_csv, write_html
        from radar.settings import load_settings

        with tempfile.TemporaryDirectory() as d:
            s = load_settings(root=ROOT)
            s.cfg["general"]["data_dir"] = str(Path(d) / "data")
            s.cfg["general"]["reports_dir"] = str(Path(d) / "reports")
            s.root = Path(d)
            res = run_scan(s, demo=True)
            self.assertTrue(res["picks"])
            self.assertTrue(all(0 <= r["upside"] <= 100 and 0 <= r["risk"] <= 100 for r in res["results"]))
            html = write_html(res, Path(d) / "r.html")
            self.assertIn("dir=\"rtl\"", html.read_text(encoding="utf-8"))
            write_csv(res, Path(d) / "r.csv")
            txt = alert_text(res)
            self.assertNotIn("*", txt)
            self.assertNotIn("#", txt)
            one = run_scan(s, demo=True, tickers=["DMO005"])
            self.assertEqual(len(one["results"]), 1)
            s.cfg["backtest"]["years"] = 3
            bt = run_backtest(s, demo=True)
            self.assertGreater(bt["metrics"]["trades"], 50)
            self.assertTrue(Path(bt["html"]).exists())


class TestPaperFill(unittest.TestCase):
    def test_pending_fills_at_next_open(self):
        with tempfile.TemporaryDirectory() as d:
            j = jmod.Journal(Path(d) / "j.sqlite")
            idx = pd.bdate_range(end=pd.Timestamp(date.today()), periods=30)
            c = pd.Series(np.linspace(20, 26, 30), index=idx)
            panel = {"Close": pd.DataFrame({"X": c}), "Open": pd.DataFrame({"X": c + 0.5}),
                     "High": pd.DataFrame({"X": c + 1}), "Low": pd.DataFrame({"X": c - 0.2})}
            sig_day = idx[5].date()
            j.paper_open([{"ticker": "X", "price": float(c.iloc[5]), "atr": 0.5,
                           "plan": {"stop": float(c.iloc[5]) - 2, "shares": 10}}], sig_day, 10)
            self.assertEqual(j.open_positions()[0]["status"], "pending")
            j.paper_update(panel, date.today(), {"trailing_stop": False, "max_hold_days": 90}, 2.5)
            pos = j.open_positions()[0]
            self.assertEqual(pos["status"], "open")
            self.assertEqual(pos["entry_date"], idx[6].date().isoformat())
            self.assertAlmostEqual(pos["entry_price"], float(c.iloc[6]) + 0.5)
            self.assertAlmostEqual(pos["entry_price"] - pos["initial_stop"], 2.0)
            summ = j.paper_summary(None)
            self.assertEqual(summ["open"], 1)
            j.close()


class TestExportAndApp(unittest.TestCase):
    def test_export_files(self):
        from radar.export import export_scan, export_tickers, write_status
        from radar.pipeline import run_scan
        from radar.settings import load_settings

        with tempfile.TemporaryDirectory() as d:
            s = load_settings(root=ROOT)
            s.cfg["general"]["data_dir"] = str(Path(d) / "data")
            s.root = Path(d)
            out = Path(d) / "site"
            res = run_scan(s, demo=True)
            export_scan(res, out)
            demo = json.loads((out / "demo" / "latest.json").read_text(encoding="utf-8"))
            self.assertTrue(demo["demo"])
            self.assertFalse((out / "latest.json").exists())
            res["demo"] = False
            export_scan(res, out)
            latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(latest["results"]), len(res["results"]))
            r0 = latest["results"][0]
            for key in ("ticker", "upside", "risk", "pros", "cons", "signals", "closes", "plan"):
                self.assertIn(key, r0)
            self.assertEqual(len(r0["closes"]), len(latest["spark_dates"]))
            self.assertEqual(json.loads((out / "index.json").read_text(encoding="utf-8"))["days"][0]["date"],
                             res["date"].isoformat())
            self.assertTrue((out / "history" / f"{res['date'].isoformat()}.json").exists())
            one = run_scan(s, demo=True, tickers=["DMO009"])
            one["demo"] = False
            export_tickers(one, out)
            self.assertEqual(json.loads((out / "tickers" / "DMO009.json").read_text(encoding="utf-8"))["result"]["ticker"], "DMO009")
            write_status(out, "scan", False, "בדיקה")
            self.assertFalse(json.loads((out / "status.json").read_text(encoding="utf-8"))["ok"])
            json.dumps(latest, allow_nan=False)  # strict JSON for the browser

    def test_workflow_copy_in_app_matches(self):
        wf = (ROOT / "ci" / "radar.yml").read_text(encoding="utf-8")
        js = (ROOT / "docs" / "workflow.js").read_text(encoding="utf-8")
        embedded = json.loads(js.split("window.RADAR_WORKFLOW = ", 1)[1].rsplit(";", 1)[0])
        self.assertEqual(wf, embedded, "run: python tools/build_app.py")
        import yaml
        doc = yaml.safe_load(wf)
        self.assertIn("workflow_dispatch", doc[True] if True in doc else doc["on"])


if __name__ == "__main__":
    unittest.main()
