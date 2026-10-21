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
            [{**politics[0], "text": "הבורסה של ICE (ICE) מדווחת על שיא"}], "ICE", "Intercontinental Exchange")), 1)


if __name__ == "__main__":
    unittest.main()
