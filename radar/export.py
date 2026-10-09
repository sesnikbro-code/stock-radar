"""Write the JSON files the phone app reads (docs/data/...).

  latest.json            today's full scan (real data)
  history/YYYY-MM-DD.json  one file per scan day, index.json lists them
  demo/latest.json       synthetic demo data (shown until the first real scan exists)
  tickers/SYM.json       on-demand single-stock analyses, tickers/index.json lists them
  backtest.json          latest backtest summary
  model.json             the probability model and its out-of-sample test (written by train.py)
  status.json            what ran last, when, and whether it worked
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from .signals import SIGNAL_LABELS
from .sources.countries import COUNTRY_HE, iso3_for
from .util import he_industry, he_sector

SCHEMA = 1


def _clean(o):
    """Make any value JSON-safe (NaN -> None, dates -> ISO, numpy -> python)."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            o = o.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, 6)
    return o


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_clean(data), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def he_country(c: str | None) -> str:
    iso = iso3_for(c)
    return COUNTRY_HE.get(iso, c or "") if iso else (c or "")


def result_json(r: dict) -> dict:
    cons = list(dict.fromkeys(r["why"]["cons"] + r.get("risk_reasons", [])))
    news = r["signals"].get("news")
    items = (news.data or {}).get("items", []) if news else []
    return {
        "ticker": r["ticker"], "name": r["name"], "pick": bool(r["pick"]),
        "price": r["price"], "upside": r["upside"], "risk": r["risk"], "coverage": r["coverage"],
        "sector": he_sector(r.get("sector")), "industry": he_industry(r.get("industry")),
        "country": he_country(r.get("country")), "market_cap": r.get("market_cap"),
        "earnings_date": r.get("earnings_date"), "move_3m": r.get("move_3m"),
        "plan": r.get("plan") or {}, "pros": r["why"]["pros"], "cons": cons,
        "signals": [{"key": k, "label": SIGNAL_LABELS[k], "score": round(r["signals"][k].score, 3),
                     "conf": round(r["signals"][k].conf, 2)}
                    for k in SIGNAL_LABELS if k in r["signals"] and r["signals"][k].conf > 0],
        "news": [{"title": i["title"], "url": i.get("url", ""), "label": i.get("label", ""),
                  "tone": 1 if i.get("s", 0) > 0 else -1 if i.get("s", 0) < 0 else 0} for i in items],
        "closes": r.get("closes", []),
        "prob": r.get("prob"), "prob_base": r.get("prob_base"), "prob_hist": r.get("prob_hist"),
        "prob_rule": r.get("prob_rule"),
    }


def scan_json(res: dict) -> dict:
    ctx = res["ctx"]
    learning = dict(res.get("learning") or {})
    if learning.get("signals"):
        learning["signals"] = [{"key": k, "label": SIGNAL_LABELS.get(k, k), "ic": v["ic"], "n": v["n"],
                                "factor": (res.get("factors") or {}).get(k)}
                               for k, v in sorted(learning["signals"].items(), key=lambda x: -x[1]["ic"])]
    return {
        "schema": SCHEMA, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": res["date"], "demo": bool(res.get("demo")),
        "counts": {"universe": res["n_universe"], "screened": res["n_screened"], "deep": res["n_deep"],
                   "picks": len(res["picks"])},
        "market": {"label": ctx.regime.get("label"), "score": ctx.regime.get("score"),
                   "notes": ctx.regime.get("notes", []), "risk_off": bool(ctx.regime.get("risk_off"))},
        "themes": [{"key": k, "he": t["he"], "ratio": t["ratio"], "escalation": t["escalation"],
                    "sectors": [{"group": g, "tilt": w} for g, w in t["tilts"].items()]}
                   for k, t in sorted(ctx.themes.items(), key=lambda x: -x[1]["ratio"])],
        "countries": [{"iso": iso, "he": COUNTRY_HE.get(iso, iso), "score": c["score"], "text": c["reasons"]}
                      for iso, c in ctx.countries.items()],
        "spark_dates": res.get("spark_dates", []),
        "results": [result_json(r) for r in res["results"]],
        "paper": res.get("paper") or {"n": 0},
        "learning": learning,
        "model": res.get("model"),
    }


def write_status(out_dir: Path, mode: str, ok: bool, message: str) -> None:
    _write(Path(out_dir) / "status.json", {
        "mode": mode, "ok": ok, "message": message,
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def export_scan(res: dict, out_dir: Path, keep_days: int = 180) -> Path:
    out_dir = Path(out_dir)
    data = scan_json(res)
    if res.get("demo"):
        _write(out_dir / "demo" / "latest.json", data)
        return out_dir / "demo" / "latest.json"
    day = res["date"].isoformat()
    _write(out_dir / "latest.json", data)
    _write(out_dir / "history" / f"{day}.json", data)
    idx = [d for d in _read(out_dir / "index.json", {"days": []}).get("days", []) if d.get("date") != day]
    idx.insert(0, {"date": day, "picks": [r["ticker"] for r in data["results"] if r["pick"]],
                   "label": data["market"]["label"]})
    idx = sorted(idx, key=lambda d: d["date"], reverse=True)
    for old in idx[keep_days:]:
        (out_dir / "history" / f"{old['date']}.json").unlink(missing_ok=True)
    _write(out_dir / "index.json", {"days": idx[:keep_days]})
    return out_dir / "latest.json"


def export_tickers(res: dict, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir) / ("demo/tickers" if res.get("demo") else "tickers")
    paths, day = [], res["date"].isoformat()
    for r in res["results"]:
        p = out_dir / f"{r['ticker']}.json"
        _write(p, {"schema": SCHEMA, "date": day, "demo": bool(res.get("demo")),
                   "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "spark_dates": res.get("spark_dates", []), "result": result_json(r)})
        paths.append(p)
    idx = [x for x in _read(out_dir / "index.json", {"tickers": []}).get("tickers", [])
           if x["ticker"] not in {r["ticker"] for r in res["results"]}]
    idx = [{"ticker": r["ticker"], "name": r["name"], "date": day} for r in res["results"]] + idx
    _write(out_dir / "index.json", {"tickers": idx[:200]})
    return paths


def export_backtest(m: dict, cfg: dict, n_tickers: int, out_dir: Path, demo: bool) -> Path:
    keep = ("periods", "years", "trades", "cagr", "spy_cagr", "total", "spy_total", "max_dd", "spy_max_dd", "sharpe",
            "avg_trade", "median_trade", "best", "worst", "beat_spy", "win_rate", "stop_rate", "explosive",
            "base_explosive", "ic_mean", "ic_t", "eq", "spy_eq", "dates", "deciles")
    data = {"schema": SCHEMA, "date": date.today(), "demo": demo, "n_tickers": n_tickers,
            "config": {k: cfg.get(k) for k in ("years", "rebalance_days", "top_n", "hold_days", "cost_pct")},
            "metrics": {k: m.get(k) for k in keep}}
    p = Path(out_dir) / ("demo/backtest.json" if demo else "backtest.json")
    _write(p, data)
    return p
