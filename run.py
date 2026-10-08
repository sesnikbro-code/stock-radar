"""Stock Radar - command line (used by the daily cloud job; works on a computer too).

  python run.py scan                      daily scan (report + alerts)
  python run.py --export docs/data scan   ...and write the data the phone app reads
  python run.py scan --demo               try everything offline with synthetic data
  python run.py ticker NVDA               deep analysis of one stock
  python run.py backtest                  walk-forward backtest of the price signals
  python run.py train                     train + test the probability model on 5 years of history
  python run.py test-alert                send a test message to Telegram / WhatsApp
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
import webbrowser
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from radar.settings import load_settings  # noqa: E402

log = logging.getLogger("radar")


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")
    for noisy in ("yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def _model_path(s, args) -> Path:
    """Where the probability model lives: next to the app data (so the app can show its test results)."""
    if getattr(args, "export", None):
        return Path(args.export) / ("demo/model.json" if args.demo else "model.json")
    return s.data_dir / ("model_demo.json" if args.demo else "model.json")


def _replay_demo_history(s, days: int, model_path=None) -> None:
    """Run the demo scan on past dates so the demo journal, paper portfolio and learning have history."""
    from radar.pipeline import run_scan

    start = date.today() - timedelta(days=days)
    d = start
    while d < date.today():
        if d.weekday() < 5:
            run_scan(s, demo=True, asof=d, model_path=model_path)
            d += timedelta(days=7)
        else:
            d += timedelta(days=1)


def cmd_scan(s, args) -> str:
    from radar.alerts import send_all
    from radar.pipeline import run_scan
    from radar.report import alert_text, write_csv, write_html

    if args.demo and args.demo_history:
        (s.data_dir / "journal_demo.sqlite").unlink(missing_ok=True)
        _replay_demo_history(s, args.demo_history, _model_path(s, args))
    res = run_scan(s, demo=args.demo, model_path=_model_path(s, args))
    if args.export:
        from radar.export import export_scan
        export_scan(res, Path(args.export))
    stamp = f"{res['date']:%Y-%m-%d}" + ("_demo" if args.demo else "")
    html = write_html(res, s.reports_dir / f"radar_{stamp}.html", s.get("news.show_original_headlines"))
    write_csv(res, s.reports_dir / f"radar_{stamp}.csv")
    text = alert_text(res, s.get("alerts.max_items"))
    print("\n" + text + "\n")
    print(f"הדוח המלא: {html}")
    if not args.no_send and not args.demo and (res["picks"] or s.get("alerts.send_when_empty")):
        sent = send_all(s, text)
        print("נשלח ל: " + ", ".join(sent) if sent else "לא הוגדרו טלגרם/וואטסאפ – ההודעה לא נשלחה.")
    if args.open:
        webbrowser.open(html.resolve().as_uri())
    kind = "סריקת הדמו" if args.demo else "הסריקה היומית"
    return f"{kind} הסתיימה: {len(res['picks'])} מניות נבחרו מתוך {res['n_deep']} שנבדקו לעומק"


def cmd_ticker(s, args) -> str:
    from radar.pipeline import run_scan
    from radar.report import write_html

    symbols = [t.upper() for t in args.symbols]
    res = run_scan(s, demo=args.demo, tickers=symbols, model_path=_model_path(s, args))
    if not res["results"]:
        raise RuntimeError(f"לא נמצאו נתונים עבור {', '.join(symbols)}. בדוק שהסימול נכון ושהמניה נסחרת בארה\"ב.")
    if args.export:
        from radar.export import export_tickers
        export_tickers(res, Path(args.export))
    for r in res["results"]:
        print(f"\n{r['ticker']} – {r['name']}")
        print(f"פוטנציאל {r['upside']:.0f}/100, סיכון {r['risk']:.0f}/100, מחיר {r['price']:.2f}$")
        for p in r["why"]["pros"]:
            print(f"  + {p}")
        for c in dict.fromkeys(r["why"]["cons"] + r["risk_reasons"]):
            print(f"  - {c}")
    path = write_html(res, s.reports_dir / f"ticker_{'_'.join(symbols)}.html", s.get("news.show_original_headlines"))
    print(f"\nהדוח: {path}")
    if args.open:
        webbrowser.open(path.resolve().as_uri())
    found = [r["ticker"] for r in res["results"]]
    return f"הניתוח של {', '.join(found)} מוכן"


def cmd_backtest(s, args) -> str:
    from radar.backtest import run_backtest

    if args.years:
        s.cfg["backtest"]["years"] = args.years
    out = run_backtest(s, demo=args.demo)
    m = out["metrics"]
    if args.export and m:
        from radar.export import export_backtest
        export_backtest(m, s.get("backtest"), out.get("n_tickers", 0), Path(args.export), args.demo)
    if m:
        print(f"\nתשואה שנתית: {m['cagr']:+.1%} (S&P 500: {m['spy_cagr']:+.1%}), ירידה מקסימלית {m['max_dd']:.1%}")
        print(f"עלו 30%+ בשלב כלשהו: {m['explosive']:.0%} מהבחירות, לעומת {m['base_explosive']:.0%} בממוצע")
    print(f"הדוח: {out['html']}")
    if args.open:
        webbrowser.open(Path(out["html"]).resolve().as_uri())
    return "הבדיקה לאחור הסתיימה" if m else "אין מספיק נתונים לבדיקה לאחור"


def cmd_train(s, args) -> str:
    from radar.train import train_model

    if args.years:
        s.cfg["train"]["years"] = args.years
    if args.max_tickers:
        s.cfg["train"]["max_tickers"] = args.max_tickers
    meta = train_model(s, demo=args.demo, out_path=_model_path(s, args))
    v = meta["validation"]
    top, base, tech = v.get("top_rate"), v.get("base"), v.get("tech_top_rate")
    print(f"\nמאגר: {meta['n_tickers']} מניות, {meta['n_rows']} תצפיות, {meta['years']} שנים")
    print(f"בדיקה על תקופה שהמודל לא ראה ({v['test_start']} עד {v['test_end']}):")
    print(f"  {v['top_n']} המובילות של המודל הגיעו ל-+{meta['target']:.0%} לפני הסטופ ב-{top:.1%} מהמקרים")
    print(f"  ממוצע כל המניות: {base:.1%}" + (f", הסינון הטכני הקיים: {tech:.1%}" if tech is not None else ""))
    print(f"  AUC: {v['auc']:.3f} (0.5 = ניחוש, 1 = מושלם)")
    lift = f"פי {top / base:.1f} מהממוצע" if top and base else "בלי יתרון ברור על הממוצע"
    return (f"מודל הסיכוי אומן על {meta['n_tickers']} מניות ב-{meta['years']} שנים. בבדיקה על שנים שלא ראה, "
            f"{v['top_n']} המובילות שלו הגיעו ל-+{meta['target']:.0%} לפני הסטופ ב-{top:.0%} מהמקרים, {lift} ({base:.0%}).")


def cmd_test_alert(s, args) -> str:
    from radar.alerts import send_all

    sent = send_all(s, "בדיקה: רדאר המניות מחובר ושולח הודעות.")
    if not sent:
        raise RuntimeError("ההודעה לא נשלחה. בדוק את המפתחות של טלגרם/וואטסאפ.")
    return "הודעת בדיקה נשלחה ל: " + ", ".join(sent)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="רדאר מניות – סורק יומי לשוק האמריקאי")
    p.add_argument("--config", default=None, help="נתיב לקובץ הגדרות (ברירת מחדל: config.yaml)")
    p.add_argument("--export", default=None, help="תיקייה לכתיבת הנתונים של האפליקציה (למשל docs/data)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("scan", help="סריקה יומית")
    a.add_argument("--demo", action="store_true", help="נתוני דמו, בלי אינטרנט")
    a.add_argument("--demo-history", type=int, default=0, help="בדמו: לשחזר היסטוריה של X ימים אחורה")
    a.add_argument("--no-send", action="store_true", help="לא לשלוח התראות")
    a.add_argument("--open", action="store_true", help="לפתוח את הדוח בדפדפן")
    b = sub.add_parser("ticker", help="ניתוח מעמיק של מניה אחת או כמה")
    b.add_argument("symbols", nargs="+")
    b.add_argument("--demo", action="store_true")
    b.add_argument("--open", action="store_true")
    c = sub.add_parser("backtest", help="בדיקה לאחור")
    c.add_argument("--years", type=int, default=None)
    c.add_argument("--demo", action="store_true")
    c.add_argument("--open", action="store_true")
    t = sub.add_parser("train", help="אימון ובדיקה של מודל הסיכוי על היסטוריה")
    t.add_argument("--years", type=int, default=None)
    t.add_argument("--max-tickers", type=int, default=None)
    t.add_argument("--demo", action="store_true")
    sub.add_parser("test-alert", help="שליחת הודעת בדיקה")
    args = p.parse_args(argv)
    s = load_settings(args.config)
    _setup_logging(s.get("general.log_level", "INFO"))
    for name in ("demo", "open", "no_send", "demo_history"):
        if not hasattr(args, name):
            setattr(args, name, False)
    handler = {"scan": cmd_scan, "ticker": cmd_ticker, "backtest": cmd_backtest, "train": cmd_train,
               "test-alert": cmd_test_alert}[args.cmd]
    mode = args.cmd + ("-demo" if args.demo else "")
    try:
        message = handler(s, args)
    except Exception as err:  # noqa: BLE001
        msg = str(err) if isinstance(err, RuntimeError) else f"שגיאה לא צפויה: {type(err).__name__}: {err}"
        log.error(msg)
        if not isinstance(err, RuntimeError):
            traceback.print_exc()
        if args.export:
            from radar.export import write_status
            write_status(Path(args.export), mode, False, msg)
        return 2
    if args.export:
        from radar.export import write_status
        write_status(Path(args.export), mode, True, message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
