"""SEC EDGAR: listed-company universe and insider trading (Form 4).

Free, no key. SEC requires a User-Agent with a contact e-mail and at most 10 requests/second.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from ..net import FOREVER, Http

log = logging.getLogger("radar.sec")

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
FULL_TXT_URL = "https://www.sec.gov/Archives/{path}"

CEO_CFO = re.compile(r"\b(ceo|cfo|chief executive|chief financial|president|chair)", re.I)


# ---------------------------------------------------------------- universe
def is_common_stock_ticker(t: str) -> bool:
    """Drop preferreds, warrants, units, rights. Keep class shares like BRK-B."""
    if not t or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", t):
        return False
    if "-" in t:
        base, suffix = t.split("-", 1)
        return len(suffix) == 1 and suffix.isalpha() and suffix not in ("W", "U", "R")
    if len(t) == 5 and t[-1] in ("W", "U", "R"):
        return False
    return True


def parse_tickers_exchange(data: dict, exchanges: list[str]) -> list[dict]:
    fields = data.get("fields", [])
    idx = {f: i for i, f in enumerate(fields)}
    allowed = {e.lower() for e in exchanges} if exchanges else None
    out, seen = [], set()
    for row in data.get("data", []):
        try:
            ticker = str(row[idx["ticker"]]).upper().strip()
            exch = row[idx["exchange"]]
            cik = int(row[idx["cik"]])
            name = row[idx["name"]]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if allowed is not None and (not exch or str(exch).lower() not in allowed):
            continue
        if ticker in seen or not is_common_stock_ticker(ticker):
            continue
        seen.add(ticker)
        out.append({"ticker": ticker, "cik": cik, "name": name, "exchange": exch})
    return out


def load_universe(http: Http, exchanges: list[str]) -> list[dict]:
    data = http.get(TICKERS_URL, as_json=True, ttl=24 * 3600)
    if not data:
        return []
    return parse_tickers_exchange(data, exchanges)


# ---------------------------------------------------------------- Form 4 parsing
def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _txt(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    node = el.find(path)
    if node is None:
        return None
    val = node.find("value")
    text = (val.text if val is not None else node.text) or ""
    text = text.strip()
    return text or None


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _flag(s: str | None) -> bool:
    return (s or "").strip().lower() in ("1", "true", "yes")


def extract_ownership_xml(text: str) -> str | None:
    """Full submission .txt files embed the XML; plain .xml documents are returned as-is."""
    if not text:
        return None
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.S)
    return m.group(0) if m else None


def parse_form4(text: str) -> dict | None:
    xml = extract_ownership_xml(text)
    if not xml:
        return None
    try:
        root = _strip_ns(ET.fromstring(xml))
    except ET.ParseError:
        return None
    issuer = root.find("issuer")
    owners = root.findall("reportingOwner")
    names, director, officer, ten_pct, titles = [], False, False, False, []
    for ow in owners:
        nm = ow.findtext("reportingOwnerId/rptOwnerName")
        if nm:
            names.append(nm.strip())
        rel = ow.find("reportingOwnerRelationship")
        if rel is not None:
            director |= _flag(rel.findtext("isDirector"))
            officer |= _flag(rel.findtext("isOfficer"))
            ten_pct |= _flag(rel.findtext("isTenPercentOwner"))
            t = rel.findtext("officerTitle")
            if t:
                titles.append(t.strip())
    plan = _flag(root.findtext("aff10b5One"))
    footnotes = " ".join((f.text or "") for f in root.iter("footnote"))
    if re.search(r"10b5-1", footnotes, re.I):
        plan = True

    txs = []
    table = root.find("nonDerivativeTable")
    if table is not None:
        for tx in table.findall("nonDerivativeTransaction"):
            code = (tx.findtext("transactionCoding/transactionCode") or "").strip()
            shares = _num(_txt(tx, "transactionAmounts/transactionShares"))
            price = _num(_txt(tx, "transactionAmounts/transactionPricePerShare"))
            ad = _txt(tx, "transactionAmounts/transactionAcquiredDisposedCode")
            d = _txt(tx, "transactionDate")
            try:
                tdate = datetime.strptime(d[:10], "%Y-%m-%d").date() if d else None
            except ValueError:
                tdate = None
            txs.append({"date": tdate, "code": code, "shares": shares or 0.0, "price": price or 0.0, "ad": ad})
    return {
        "issuer_cik": int(issuer.findtext("issuerCik")) if issuer is not None and (issuer.findtext("issuerCik") or "").strip().isdigit() else None,
        "symbol": (issuer.findtext("issuerTradingSymbol") or "").strip().upper() if issuer is not None else "",
        "owner": "; ".join(names),
        "is_director": director,
        "is_officer": officer,
        "is_ten_pct": ten_pct,
        "title": "; ".join(titles),
        "planned": plan,
        "transactions": txs,
    }


def flatten_filings(filings: list[dict]) -> list[dict]:
    """One row per transaction with owner metadata attached."""
    rows = []
    for f in filings:
        for tx in f.get("transactions", []):
            rows.append({
                **tx,
                "owner": f.get("owner", ""),
                "title": f.get("title", ""),
                "is_officer": f.get("is_officer", False),
                "is_director": f.get("is_director", False),
                "is_ten_pct": f.get("is_ten_pct", False),
                "planned": f.get("planned", False),
                "top_exec": bool(CEO_CFO.search(f.get("title", "") or "")),
            })
    return rows


# ---------------------------------------------------------------- fetching
def company_form4(http: Http, cik: int, lookback_days: int, max_filings: int = 40) -> list[dict]:
    subs = http.get(SUBMISSIONS_URL.format(cik=cik), as_json=True, ttl=6 * 3600)
    if not subs:
        return []
    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    out = []
    for i, form in enumerate(forms):
        if form not in ("4", "4/A"):
            continue
        fdate = recent["filingDate"][i]
        if fdate < since:
            continue
        acc = recent["accessionNumber"][i].replace("-", "")
        doc = recent["primaryDocument"][i].split("/")[-1]
        url = ARCHIVE_URL.format(cik=cik, acc=acc, doc=doc)
        try:
            text = http.get(url, ttl=FOREVER)
        except Exception as e:  # noqa: BLE001
            log.debug("form4 fetch failed %s: %s", url, e)
            continue
        parsed = parse_form4(text or "")
        if parsed:
            parsed["filing_date"] = fdate
            out.append(parsed)
        if len(out) >= max_filings:
            break
    return out


def parse_daily_form_index(text: str) -> list[dict]:
    rows, started = [], False
    for line in text.splitlines():
        if not started:
            if line.startswith("-----"):
                started = True
            continue
        parts = line.split()
        if len(parts) < 5 or parts[0] not in ("4", "4/A"):
            continue
        path = parts[-1]
        m = re.search(r"(\d{10}-\d{2}-\d{6})\.txt$", path)
        if not m:
            continue
        rows.append({"form": parts[0], "cik": parts[-3], "date": parts[-2], "path": path, "accession": m.group(1)})
    return rows


def _business_days_back(n: int, today: date | None = None) -> list[date]:
    d = today or date.today()
    out = []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def discover_insider_buying(http: Http, days: int, max_filings: int) -> dict[str, dict]:
    """Scan all recent Form 4 filings market-wide and aggregate open-market purchases per issuer."""
    filings, seen = [], set()
    for d in _business_days_back(days):
        q = (d.month - 1) // 3 + 1
        url = DAILY_INDEX_URL.format(y=d.year, q=q, ymd=d.strftime("%Y%m%d"))
        try:
            text = http.get(url, ttl=FOREVER, cache_404=False)  # index may not be published yet
        except Exception as e:  # noqa: BLE001
            log.warning("SEC daily index לא זמין עבור %s: %s", d, e)
            continue
        for row in parse_daily_form_index(text or ""):
            if row["accession"] in seen:
                continue
            seen.add(row["accession"])
            filings.append(row)
    filings = filings[:max_filings]
    log.info("סורק %d דיווחי Form 4 מהימים האחרונים (קניות מנהלים בכל השוק)...", len(filings))
    agg: dict[str, dict] = {}
    for row in filings:
        try:
            text = http.get(FULL_TXT_URL.format(path=row["path"]), ttl=FOREVER)
        except Exception:  # noqa: BLE001
            continue
        f = parse_form4(text or "")
        if not f or not f["symbol"]:
            continue
        buys = [t for t in f["transactions"] if t["code"] == "P" and (t["ad"] or "A") == "A"]
        if not buys:
            continue
        value = sum(t["shares"] * t["price"] for t in buys)
        a = agg.setdefault(f["symbol"], {"value": 0.0, "insiders": set(), "filings": []})
        a["value"] += value
        a["insiders"].add(f["owner"])
        f["filing_date"] = row["date"]
        a["filings"].append(f)
    for a in agg.values():
        a["n_insiders"] = len(a.pop("insiders"))
    return agg
