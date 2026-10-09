"""Daily Hebrew report (HTML, mobile-friendly, cards instead of tables), CSV export and plain-text alert."""
from __future__ import annotations

import csv
import html
from datetime import date
from pathlib import Path

from .signals import SIGNAL_LABELS
from .sources.countries import COUNTRY_HE, iso3_for
from .util import he_industry, he_sector, money_he, pct

DISCLAIMER = ("המערכת היא כלי עזר לסינון ולמחקר, לא ייעוץ השקעות. ציון גבוה פירושו סיכוי טוב מהממוצע לפי "
              "הנתונים, לא הבטחה. מניות עם פוטנציאל לעלייה חדה יכולות גם לרדת חדה. החלטת ההשקעה והאחריות שלך.")

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5d6472;--line:#e3e6eb;--accent:#1d5fd1;
--pos:#14804a;--neg:#c2362b;--warn:#a15c00;--chip:#eef1f6;--meter:#e8ebf0}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--ink:#e9ecf1;--muted:#9aa3b2;
--line:#2a2f38;--accent:#6ea0ff;--pos:#3ccf85;--neg:#ff6b5e;--warn:#f0a23c;--chip:#222731;--meter:#262b34}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,"Segoe UI",Arial,"Noto Sans Hebrew",sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:26px;margin:8px 0 2px}h2{font-size:19px;margin:28px 0 10px}h3{margin:0;font-size:20px}
.sub{color:var(--muted);font-size:14px}
.badge{display:inline-block;background:var(--warn);color:#fff;border-radius:6px;padding:2px 8px;font-size:13px;margin-inline-start:8px}
.note{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;font-size:14px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
.head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
.tk{font-weight:700;letter-spacing:.5px;direction:ltr;unicode-bidi:isolate}
.meters{display:flex;gap:14px;margin:12px 0 4px;flex-wrap:wrap}
.meter{flex:1;min-width:150px}.meter .lbl{display:flex;justify-content:space-between;font-size:13px;color:var(--muted)}
.bar{height:8px;background:var(--meter);border-radius:6px;overflow:hidden;margin-top:4px}
.bar>i{display:block;height:100%;border-radius:6px}
.facts{display:flex;flex-wrap:wrap;gap:6px;font-size:14px;color:var(--muted);margin:10px 0}
.facts>div{background:var(--chip);border-radius:8px;padding:4px 9px}.facts b{color:var(--ink);font-weight:600}
ul{margin:6px 0;padding-inline-start:20px}li{margin:3px 0}
.pros li::marker{color:var(--pos)}.cons li::marker{color:var(--neg)}
.sec{font-size:13px;font-weight:700;color:var(--muted);margin-top:10px}
.sig{display:grid;grid-template-columns:130px 1fr 44px;gap:8px;align-items:center;font-size:13px;margin:3px 0}
.sig .bar{margin:0}.sig span:last-child{text-align:left;direction:ltr;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:var(--chip);border-radius:999px;padding:4px 10px;font-size:14px}
.up{color:var(--pos)}.down{color:var(--neg)}
.mini{padding:10px 0;border-bottom:1px solid var(--line);font-size:14px}.mini:last-child{border:0}
.heads a{color:var(--accent);text-decoration:none;font-size:13px;direction:ltr;unicode-bidi:isolate}
details summary{cursor:pointer;color:var(--accent);font-size:14px;margin-top:8px}
footer{color:var(--muted);font-size:13px;margin:30px 0}
"""


def _d(iso: str) -> str:
    try:
        return date.fromisoformat(str(iso)).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso)


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _meter(label: str, value: float, color: str) -> str:
    return (f'<div class="meter"><div class="lbl"><span>{e(label)}</span><b>{value:.0f}/100</b></div>'
            f'<div class="bar"><i style="width:{max(2, min(100, value)):.0f}%;background:{color}"></i></div></div>')


def _signal_rows(sigs: dict) -> str:
    rows = []
    for name, label in SIGNAL_LABELS.items():
        s = sigs.get(name)
        if s is None or s.conf <= 0:
            continue
        w = abs(s.score) * 100
        color = "var(--pos)" if s.score >= 0 else "var(--neg)"
        rows.append(f'<div class="sig"><span>{e(label)}</span><div class="bar"><i style="width:{max(w, 2):.0f}%;'
                    f'background:{color}"></i></div><span>{s.score:+.2f}</span></div>')
    return "".join(rows)


def he_country(c: str | None) -> str:
    iso = iso3_for(c)
    return COUNTRY_HE.get(iso, c or "") if iso else (c or "")


def prob_label(r: dict) -> str:
    rule = r.get("prob_rule") or {}
    if rule.get("mode") == "pct" and rule.get("pct"):
        return f"סיכוי לעלות {rule['pct']:.0%} לפני הסטופ"
    return "סיכוי להגיע ליעד לפני הסטופ"


def _card(r: dict, show_titles: bool) -> str:
    plan = r.get("plan") or {}
    facts = [f"מחיר <b>{r['price']:.2f}$</b>"]
    if r.get("prob") is not None and r.get("prob_base"):
        facts.insert(0, f"{prob_label(r)} <b>{r['prob']:.0%}</b> (ממוצע {r['prob_base']:.0%})")
    if plan.get("stop"):
        facts.append(f"סטופ מוצע <b>{plan['stop']:.2f}$</b> (<bdi>{pct(plan['stop_pct'])}</bdi>)")
    if plan.get("target"):
        facts.append(f"יעד מוצע <b>{plan['target']:.2f}$</b> (<bdi>{pct(plan['target_pct'], sign=True)}</bdi>)")
    if plan.get("shares"):
        facts.append(f"פוזיציה מוצעת <b>{plan['shares']} מניות</b> בכ־{money_he(plan['value'])}")
        facts.append(f"הפסד מקסימלי בסטופ <b>{money_he(plan['risk_amount'])}</b>")
    elif plan:
        facts.append("<b>מחיר המניה גבוה ביחס לגודל החשבון שהוגדר</b>")
    if r.get("move_3m"):
        facts.append(f"תנודה טיפוסית ל־3 חודשים <b>±{pct(r['move_3m'])}</b>")
    if r.get("market_cap"):
        facts.append(f"שווי שוק <b>{money_he(r['market_cap'])}</b>")
    if isinstance(r.get("earnings_date"), date):
        facts.append(f"דוח הבא <b>{r['earnings_date']:%d/%m/%Y}</b>")
    where = " · ".join(x for x in (he_sector(r.get("sector")), he_industry(r.get("industry")),
                                   he_country(r.get("country"))) if x)
    pros = "".join(f"<li>{e(x)}</li>" for x in r["why"]["pros"]) or "<li>אין נימוק בולט</li>"
    cons_list = r["why"]["cons"] + r.get("risk_reasons", [])
    cons = "".join(f"<li>{e(x)}</li>" for x in dict.fromkeys(cons_list))
    heads = ""
    items = (r["signals"].get("news").data or {}).get("items") if r["signals"].get("news") else None
    if show_titles and items:
        links = "".join(f'<li><a href="{e(i["url"])}" target="_blank" rel="noopener">{e(i["title"])}</a></li>'
                        if i.get("url") else f"<li><span class='heads'>{e(i['title'])}</span></li>" for i in items)
        heads = f'<details class="heads"><summary>כותרות המקור (באנגלית)</summary><ul>{links}</ul></details>'
    return f"""
<div class="card">
  <div class="head"><div><h3><span class="tk">{e(r['ticker'])}</span> · {e(r['name'])}</h3>
  <div class="sub">{e(where)}</div></div></div>
  <div class="meters">{_meter('פוטנציאל עלייה', r['upside'], 'var(--pos)')}{_meter('סיכון', r['risk'], 'var(--neg)')}</div>
  <div class="facts">{''.join(f'<div>{x}</div>' for x in facts)}</div>
  <div class="sec">למה נבחרה</div><ul class="pros">{pros}</ul>
  {f'<div class="sec">שים לב</div><ul class="cons">{cons}</ul>' if cons else ''}
  <details><summary>פירוט כל האותות</summary>{_signal_rows(r['signals'])}
  <div class="sub">ציון בין ‎-1 (שלילי) ל־‎+1 (חיובי). כיסוי נתונים: {pct(r['coverage'])}</div></details>
  {heads}
</div>"""


def _market_section(res: dict) -> str:
    ctx = res["ctx"]
    reg = ctx.regime
    parts = [f'<div class="card"><h3>מצב השוק: {e(reg.get("label"))}</h3>',
             "<ul>" + "".join(f"<li>{e(n)}</li>" for n in reg.get("notes", [])) + "</ul>"]
    if ctx.themes:
        chips = []
        for th in sorted(ctx.themes.values(), key=lambda x: -x["ratio"]):
            cls = "down" if th["ratio"] >= 1.25 else "up" if th["ratio"] <= 0.8 else ""
            chips.append(f'<span class="chip {cls}">{e(th["he"])} · פי {th["ratio"]:.1f}</span>')
        parts.append('<div class="sec">מלחמות ומתיחות – היקף הסיקור בשבוע האחרון ביחס לחודשיים שלפניו</div>'
                     f'<div class="chips">{"".join(chips)}</div>')
    if ctx.countries:
        parts.append('<div class="sec">מדינות (לחברות זרות הנסחרות בארה"ב)</div><ul>')
        for c in ctx.countries.values():
            parts.append(f"<li>{e(c['reasons'][0])}</li>")
        parts.append("</ul>")
    parts.append("</div>")
    return "".join(parts)


def _paper_section(p: dict) -> str:
    if not p or not p.get("n"):
        return ""
    lines = [f"<li>מאז {_d(p['since'])}: {p['n']} עסקאות ({p['open']} פתוחות, {p['closed']} סגורות)</li>",
             f"<li>רווח/הפסד: {money_he(p['realized'] + p['unrealized'])} "
             f"({pct(p.get('return_on_invested'), 1, True)} על ההון שהושקע)</li>"]
    if p.get("spy_ret") is not None:
        lines.append(f"<li>S&P 500 באותה תקופה: {pct(p['spy_ret'], 1, True)}</li>")
    if p.get("win_rate") is not None:
        lines.append(f"<li>אחוז עסקאות מרוויחות: {pct(p['win_rate'])} · רווח ממוצע {pct(p.get('avg_win'), 1, True)} · "
                     f"הפסד ממוצע {pct(p.get('avg_loss'), 1, True)}</li>")
    pos = "".join(f'<div class="mini"><b class="tk">{e(x["ticker"])}</b> · <span class="{"up" if x["ret"] >= 0 else "down"}">'
                  f'<bdi>{pct(x["ret"], 1, True)}</bdi></span><div class="sub">כניסה {x["entry"]:.2f}$ · עכשיו '
                  f'{(x["last"] or x["entry"]):.2f}$ · סטופ {x["stop"]:.2f}$</div></div>' for x in p.get("open_positions", []))
    return (f'<h2>תיק מסחר על נייר</h2><div class="card"><ul>{"".join(lines)}</ul>'
            f'{"<div class=sec>פוזיציות פתוחות</div>" + pos if pos else ""}</div>')


def _learning_section(lr: dict, factors: dict) -> str:
    if not lr or not lr.get("n"):
        return ('<h2>מה עובד?</h2><div class="note">המערכת תתחיל למדוד אילו אותות באמת עובדים אחרי שיצטברו '
                'המלצות בנות 30 יום ומעלה. ככל שתריץ יותר ימים, המשקלות יכוילו לפי התוצאות.</div>')
    items = []
    pk = lr.get("picks", {})
    if pk.get("n"):
        items.append(f"<li>המניות שנבחרו ({pk['n']}): ביצועים עודפים ממוצעים {pct(pk['avg_excess'], 1, True)} מול "
                     f"S&P 500 ב־{lr['horizon']} יום, {pct(pk['hit_rate'])} ניצחו את המדד, "
                     f"{pct(pk['explosive_rate'])} עלו 30%+ בשלב כלשהו</li>")
    al = lr.get("all", {})
    if al:
        items.append(f"<li>לשם השוואה, כל המניות שנבדקו: {pct(al['avg_excess'], 1, True)}, "
                     f"{pct(al['explosive_rate'])} עלו 30%+</li>")
    for name, st in sorted(lr.get("signals", {}).items(), key=lambda x: -x[1]["ic"]):
        f = factors.get(name)
        adj = f" → משקל ×{f:.2f}" if f else ""
        items.append(f"<li>{e(SIGNAL_LABELS.get(name, name))}: מתאם {st['ic']:+.2f} ({st['n']} דגימות){adj}</li>")
    return ('<h2>מה עובד? (בדיקה מול תוצאות אמת)</h2><div class="card"><ul>' + "".join(items) +
            '</ul><div class="sub">מתאם חיובי = ציון גבוה באות הזה הקדים ביצועים טובים יותר.</div></div>')


def write_html(res: dict, path: Path, show_titles: bool = True) -> Path:
    d = res["date"]
    picks = res["picks"]
    others = [r for r in res["results"] if not r["pick"]][:15]
    title = "ניתוח מניה" if res.get("single") else "רדאר מניות"
    demo = '<span class="badge">נתוני דמו – לא מניות אמיתיות</span>' if res.get("demo") else ""
    pick_html = "".join(_card(r, show_titles) for r in (res["results"] if res.get("single") else picks))
    if not pick_html:
        pick_html = '<div class="note">אף מניה לא עברה היום את סף הפוטנציאל והסיכון. זה תקין – לפעמים הדבר הנכון הוא לחכות.</div>'
    watch = "".join(f'<div class="mini"><b class="tk">{e(r["ticker"])}</b> · {e(r["name"])}'
                    f'<div class="sub">פוטנציאל {r["upside"]:.0f} · סיכון {r["risk"]:.0f}</div>'
                    + (f'<div>{e(r["why"]["pros"][0])}</div>' if r["why"]["pros"] else "") + "</div>" for r in others)
    body = f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} {d:%d/%m/%Y}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>{title}{demo}</h1><div class="sub">{d:%d/%m/%Y} · נסרקו {res['n_universe']} מניות, {res['n_screened']} עברו את
מסנני הנזילות, {res['n_deep']} נבדקו לעומק</div>
<p class="note">{DISCLAIMER}</p>
{'' if res.get('single') else '<h2>תמונת מצב</h2>' + _market_section(res)}
<h2>{'התוצאה' if res.get('single') else f'המניות שנבחרו היום ({len(picks)})'}</h2>{pick_html}
{f'<h2>ברשימת המעקב</h2><div class="card">{watch}</div>' if watch and not res.get('single') else ''}
{'' if res.get('single') else _paper_section(res.get('paper')) + _learning_section(res.get('learning'), res.get('factors', {}))}
<footer>מקורות: מחירים, אנליסטים, אופציות ושורט – Yahoo Finance · קניות מנהלים – SEC (Form 4) · מאקרו – FRED ·
מלחמות ומתיחות – GDELT · מדינות – IMF / הבנק העולמי · חוזים ממשלתיים – USASpending.gov</footer>
</div></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def write_csv(res: dict, path: Path) -> Path:
    cols = ["ticker", "name", "pick", "upside", "risk", "price", "stop", "target", "prob", "shares", "sector",
            "industry", "country", "market_cap"] + list(SIGNAL_LABELS)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in res["results"]:
            plan = r.get("plan") or {}
            w.writerow([r["ticker"], r["name"], int(r["pick"]), r["upside"], r["risk"], round(r["price"], 2),
                        plan.get("stop"), plan.get("target"),
                        round(r["prob"], 4) if r.get("prob") is not None else "", plan.get("shares"),
                        r["sector"], r["industry"], r["country"],
                        r["market_cap"]] + [round(r["signals"][k].score, 3) if k in r["signals"] and r["signals"][k].conf > 0
                                            else "" for k in SIGNAL_LABELS])
    return path


def alert_text(res: dict, max_items: int = 10) -> str:
    """Plain text for WhatsApp/Telegram: no markdown, no asterisks, dashes only."""
    d = res["date"]
    ctx = res["ctx"]
    lines = [f"רדאר מניות – {d:%d/%m/%Y}" + (" (דמו)" if res.get("demo") else ""),
             f"מצב השוק: {ctx.regime.get('label')}"]
    hot = [th["he"] for th in sorted(ctx.themes.values(), key=lambda x: -x["ratio"]) if th["ratio"] >= 1.25]
    if hot:
        lines.append("מוקדי מתיחות בעלייה: " + ", ".join(hot[:3]))
    lines.append("")
    picks = res["picks"][:max_items]
    if not picks:
        lines.append("אף מניה לא עברה היום את הסף. לפעמים הדבר הנכון הוא לחכות.")
    for i, r in enumerate(picks, 1):
        plan = r.get("plan") or {}
        lines.append(f"{i}. {r['ticker']} – {r['name']}" + (f" ({he_industry(r['industry'])})" if r.get("industry") else ""))
        line = f"פוטנציאל {r['upside']:.0f}, סיכון {r['risk']:.0f}, מחיר {r['price']:.2f}$"
        if plan.get("stop"):
            line += f", סטופ מוצע {plan['stop']:.2f}$"
        if plan.get("target"):
            line += f", יעד {plan['target']:.2f}$"
        lines.append(line)
        if r.get("prob") is not None and r.get("prob_base"):
            lines.append(f"{prob_label(r)}: {r['prob']:.0%} (ממוצע {r['prob_base']:.0%})")
        for p in r["why"]["pros"][:3]:
            lines.append(f"- {p}")
        warn = (r["why"]["cons"] + r.get("risk_reasons", []))[:1]
        if warn:
            lines.append(f"- שים לב: {warn[0]}")
        lines.append("")
    p = res.get("paper") or {}
    if p.get("n"):
        lines.append(f"תיק על נייר: {pct(p.get('return_on_invested'), 1, True)} מאז {_d(p['since'])}"
                     + (f" (S&P 500: {pct(p['spy_ret'], 1, True)})" if p.get("spy_ret") is not None else ""))
    lines.append("לא ייעוץ השקעות – כלי עזר בלבד.")
    return "\n".join(lines).replace("*", "")
