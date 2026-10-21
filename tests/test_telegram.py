"""Tests for reading public Telegram channels (radar/sources/telegram.py).  Run:  python -m unittest discover tests"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.sources import telegram  # noqa: E402


def _post(pid, text, days_ago=0):
    d = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()
    return (f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message '
            f'text_not_supported_wrap js-widget_message" data-post="Tisraelnewss/{pid}" data-view="x">'
            f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>'
            f'<div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" '
            f'href="https://t.me/Tisraelnewss/{pid}"><time datetime="{d}" class="time">09:56</time></a></div>'
            f'</div></div>')


PAGE1 = "<html><body>" + "".join([
    _post(38508, "<b>ענקיות הטכנולוגיה מסין מסתערות על השבבים של אנבידיה</b><br/>פרטים בהמשך &amp; עוד"),
    _post(38509, "ג'נרל מוטורס מכה את הציפיות ברווח ובהכנסות", 1),
    _post(38511, "סיילספורס רוכשת את חברת הטכנולוגיה Braze תמורת 5.66 מיליארד דולר", 1),
    _post(38512, "מניית CBRL זינקה 8% – Cracker Barrel מפתיעה", 2),
    _post(38513, "פרפלקסיטי מאפשרת להפעיל את סוכן ה-AI על המק, וגם אפליקציה חדשה", 2),
]) + "</body></html>"
PAGE2 = "<html><body>" + _post(38400, "באנבידיה מודים: הביקוש חזק", 6) + _post(38390, "ישן: נטפליקס", 12) + "</body></html>"


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append(params)
        return PAGE1 if not params else PAGE2 if params.get("before") == 38508 else ""


class TestTelegram(unittest.TestCase):
    def test_parse_page(self):
        posts = telegram.parse_page(PAGE1, "Tisraelnewss")
        self.assertEqual([p["id"] for p in posts], [38508, 38509, 38511, 38512, 38513])
        self.assertEqual(posts[0]["text"].split("\n")[0], "ענקיות הטכנולוגיה מסין מסתערות על השבבים של אנבידיה")
        self.assertIn("&", posts[0]["text"])
        self.assertEqual(posts[0]["url"], "https://t.me/Tisraelnewss/38508")
        self.assertIsNotNone(posts[0]["date"].tzinfo)

    def test_fetch_pages_back_until_the_period_ends(self):
        http = FakeHttp()
        posts = telegram.fetch_channel(http, "Tisraelnewss", days=7, max_pages=5)
        self.assertEqual(http.calls, [None, {"before": 38508}])          # second page, then the period was over
        self.assertEqual([p["id"] for p in posts], [38513, 38512, 38511, 38509, 38508, 38400])   # 12 days ago: out

    def test_mentions(self):
        posts = telegram.fetch_channel(FakeHttp(), "Tisraelnewss", days=7)
        ids = lambda t, n=None: [m["u"].rsplit("/", 1)[1] for m in telegram.mentions(posts, t, n)]  # noqa: E731
        self.assertEqual(ids("NVDA", "NVIDIA CORP"), ["38508", "38400"])   # Hebrew name, also with a prefix (באנבידיה)
        self.assertEqual(ids("GM", "General Motors Company"), ["38509"])
        self.assertEqual(ids("CRM", "Salesforce, Inc."), ["38511"])
        self.assertEqual(ids("BRZE", "Braze, Inc."), ["38511"])               # English name inside a Hebrew post
        self.assertEqual(ids("CBRL", "Cracker Barrel Old Country Store, Inc."), ["38512"])
        self.assertEqual(ids("AAPL", "Apple Inc."), [])                        # 'אפליקציה' is not Apple
        self.assertEqual(ids("AI", "C3.ai, Inc."), [])                         # 'AI' is not a ticker here
        m = telegram.mentions(posts, "NVDA", "NVIDIA CORP")[0]
        self.assertEqual(m["t"], "ענקיות הטכנולוגיה מסין מסתערות על השבבים של אנבידיה")
        self.assertEqual(m["c"], "Tisraelnewss")

    def test_scan_shows_mentions(self):
        import tempfile
        from radar.pipeline import run_scan
        from radar.settings import load_settings
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(root=ROOT)
            s.cfg["general"]["data_dir"] = str(Path(d) / "data")
            s.root = Path(d)
            res = run_scan(s, demo=True)
            got = {r["ticker"]: r for r in res["results"]}
            if "DMO008" in got:
                self.assertEqual(got["DMO008"]["tg_n"], 1)
                self.assertIn("DMO008", got["DMO008"]["tg"][0]["t"])
            self.assertTrue(all(r["tg_n"] is not None for r in res["results"]))
            self.assertEqual(res["health"]["telegram"], 2)
            from radar.export import scan_json
            j = scan_json(res)
            t = j["telegram"]
            self.assertEqual(t["labels"]["Tisraelnewss"], "ביזנס 10")
            self.assertEqual(t["labels"]["weralllocos"], "המשקיע ששוקע")
            self.assertEqual(t["labels"]["Mivzakhon"], "מבזקהון")
            self.assertEqual(t["channels"], ["Tisraelnewss", "weralllocos", "Mivzakhon"])
            self.assertEqual(t["per"], {"Tisraelnewss": 1, "weralllocos": 0, "Mivzakhon": 1})
            self.assertEqual(t["posts"], 2)
            self.assertEqual(t["stats"]["Tisraelnewss"]["n"], 1)
            if "DMO008" in got:
                x = next(x for x in t["stocks"] if x["ticker"] == "DMO008")
                self.assertEqual(j["results"][x["i"]]["ticker"], "DMO008")    # opens the right stock

    def test_several_channels(self):
        self.assertEqual(telegram.channel_name("https://t.me/s/Mivzakhon/"), "Mivzakhon")
        self.assertEqual(telegram.channel_name("@weralllocos"), "weralllocos")
        now = datetime.now(timezone.utc)
        a = [{"id": 5, "date": now - timedelta(days=3), "channel": "A", "text": "נטפליקס מפתיעה", "url": "u/a5"}]
        b = [{"id": 900, "date": now - timedelta(days=1), "channel": "B", "text": "דוח של נטפליקס", "url": "u/b900"},
             {"id": 901, "date": now, "channel": "B", "text": "פאלו אלטו (PANW) נכנסת למדד", "url": "u/b901"}]
        posts = a + b      # one channel after the other, as they were read
        got = telegram.mentions(posts, "NFLX", "Netflix, Inc.")
        self.assertEqual([m["u"] for m in got], ["u/b900", "u/a5"])           # newest first across channels
        self.assertEqual([m["c"] for m in got], ["B", "A"])
        self.assertEqual(len(telegram.mentions(posts, "PANW", "Palo Alto Networks, Inc.")), 1)
        politics = [{"id": 1, "date": now, "channel": "C", "text": "סוכני ICE עצרו מפגינים ב-NYC", "url": "u/c1"}]
        self.assertEqual(telegram.mentions(politics, "ICE", "Intercontinental Exchange, Inc."), [])
        self.assertEqual(len(telegram.mentions(
            [{**politics[0], "text": "הבורסה של $ICE מדווחת על שיא"}], "ICE", "Intercontinental Exchange")), 1)

    def test_acronyms_are_not_tickers(self):
        now = datetime.now(timezone.utc)
        jio = [{"id": 1, "date": now, "channel": "A", "url": "u/1",
                "text": "ביקושי ענק להנפקת ג'יו\nבין המשקיעים: קרן GIC מסינגפור ו-ADIA"}]
        self.assertEqual(telegram.mentions(jio, "GIC", "GLOBAL INDUSTRIAL Co"), [])      # GIC the fund, not the stock
        ai = [{**jio[0], "text": "מירוץ הבינה המלאכותית (AI) מאיץ"}]
        self.assertEqual(telegram.mentions(ai, "AI", "C3.ai, Inc."), [])
        ok = [{**jio[0], "text": "מניית GIC עלתה 5%"}, {**jio[0], "id": 2, "text": "AMD מזנקת"},
              {**jio[0], "id": 3, "text": "גלובל: (GIC) מעדכנת תחזית"}]
        self.assertEqual(len(telegram.mentions(ok, "GIC", "GLOBAL INDUSTRIAL Co")), 2)
        self.assertEqual(len(telegram.mentions(ok, "AMD", "ADVANCED MICRO DEVICES INC")), 1)
        self.assertEqual(telegram.explicit_tickers(ai + ok + [{**jio[0], "text": "קניתי $PLTR ו-(CEO)"}]),
                         {"GIC", "PLTR"})

    def test_overview_and_channel_stats(self):
        now = datetime.now(timezone.utc)
        posts = [{"id": 3, "date": now, "channel": "A", "url": "u/a3", "text": "אנבידיה ואפל מובילות"},
                 {"id": 2, "date": now - timedelta(days=2), "channel": "A", "url": "u/a2", "text": "אנבידיה: דוח חזק"},
                 {"id": 9, "date": now - timedelta(days=1), "channel": "B", "url": "u/b9", "text": "פאלו אלטו (PANW)"}]
        names = {"NVDA": "NVIDIA CORP", "AAPL": "Apple Inc.", "PANW": "Palo Alto Networks", "MSFT": "Microsoft"}
        ov = telegram.week_overview(posts, names)
        self.assertEqual([(x["ticker"], x["n"]) for x in ov], [("NVDA", 2), ("AAPL", 1), ("PANW", 1)])
        st = telegram.channel_stats(posts, ["A", "B", "C"])
        self.assertEqual((st["A"]["n"], st["B"]["n"], st["C"]["n"]), (2, 1, 0))
        self.assertEqual(st["A"]["last_u"], "u/a3")
        self.assertEqual(st["A"]["last_t"], "אנבידיה ואפל מובילות")
        self.assertIsNone(st["C"]["to"])


    def test_log_and_manual_pull(self):
        import json
        import tempfile
        from radar.export import export_scan
        from radar.pipeline import run_scan
        from radar.settings import load_settings
        from radar.tgwatch import pull
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(root=ROOT)
            s.cfg["general"]["data_dir"] = str(Path(d) / "data")
            s.root = Path(d)
            out = Path(d) / "docs"
            res = run_scan(s, demo=True)
            export_scan(res, out)
            f = json.loads((out / "demo" / "telegram.json").read_text(encoding="utf-8"))
            self.assertEqual(f["mode"], "scan")
            self.assertEqual(f["history"]["reads"], 1)
            self.assertEqual(f["per"], {"Tisraelnewss": 1, "weralllocos": 0, "Mivzakhon": 1})
            n1 = f["history"]["n_mentions"]
            msg = pull(s, out, demo=True)                      # the manual pull
            self.assertIn("המשיכה מהטלגרם הסתיימה", msg)
            self.assertIn("המשקיע ששוקע", msg)                 # the channel that gave nothing is named
            f2 = json.loads((out / "demo" / "telegram.json").read_text(encoding="utf-8"))
            self.assertEqual(f2["mode"], "manual")
            self.assertEqual(f2["history"]["reads"], 2)
            self.assertEqual(f2["history"]["n_mentions"], n1)  # the same posts again: not counted twice
            self.assertEqual([r["mode"] for r in f2["reads"]], ["manual", "scan"])
            latest = json.loads((out / "demo" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["telegram"]["mode"], "manual")
            self.assertTrue(all(r["tg_n"] is not None for r in latest["results"]))

    def test_journal_log(self):
        import tempfile
        from radar.journal import Journal
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "j.sqlite")
            m = [{"u": "u/1", "ticker": "NVDA", "name": "NVIDIA", "c": "A", "d": "2026-10-20T10:00:00+00:00", "t": "x"},
                 {"u": "u/2", "ticker": "NVDA", "name": "NVIDIA", "c": "B", "d": "2026-10-21T10:00:00+00:00", "t": "y"},
                 {"u": "u/2", "ticker": "AAPL", "name": "Apple", "c": "B", "d": "2026-10-21T10:00:00+00:00", "t": "y"}]
            self.assertEqual(j.tg_record("2026-10-21T08:00:00+00:00", "scan", {"A": 5, "B": 3}, m), 3)
            self.assertEqual(j.tg_record("2026-10-21T09:00:00+00:00", "manual", {"A": 5, "B": 3}, m[:2]), 0)
            h = j.tg_stocks()
            self.assertEqual((h["n_mentions"], h["n_stocks"], h["reads"]), (3, 2, 2))
            nv = h["stocks"][0]
            self.assertEqual((nv["ticker"], nv["n"], nv["channels"]), ("NVDA", 2, {"A": 1, "B": 1}))
            self.assertEqual(nv["tg"][0]["u"], "u/2")                        # newest first
            self.assertEqual(j.tg_reads()[0]["mode"], "manual")
            j.close()


if __name__ == "__main__":
    unittest.main()
