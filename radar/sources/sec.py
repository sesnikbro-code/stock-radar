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
            after = _num(_txt(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction"))
            txs.append({"date": tdate, "code": code, "shares": shares or 0.0, "price": price or 0.0, "ad": ad,
                        "shares_after": after})
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
        fd = f.get("filing_date")
        try:
            fd = date.fromisoformat(str(fd)[:10]) if fd and not isinstance(fd, date) else fd
        except ValueError:
            fd = None
        for tx in f.get("transactions", []):
            rows.append({
                **tx,
                "filing_date": fd or tx.get("date"),
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


def company_filings(http: Http, cik: int, ttl: float = 6 * 3600) -> dict | None:
    """The company's recent filing list (form, filingDate, items, ...). Same request and cache as the insider and
    earnings checks, so this normally costs nothing extra. Includes filings where the company is the subject
    (e.g. a tender offer or merger communication filed by a buyer)."""
    subs = http.get(SUBMISSIONS_URL.format(cik=cik), as_json=True, ttl=ttl)
    if not subs:
        return None
    return (subs.get("filings") or {}).get("recent") or None


def _earnings_rows(block: dict, since: str) -> list[str]:
    forms, items, dates = block.get("form", []), block.get("items", []), block.get("filingDate", [])
    out = []
    for i, form in enumerate(forms):
        if form != "8-K" or i >= len(items) or i >= len(dates):
            continue
        if "2.02" in str(items[i] or "") and dates[i] >= since:
            out.append(dates[i])
    return out


def earnings_filing_dates(http: Http, cik: int, since: date, ttl: float = 6 * 3600) -> list[date] | None:
    """Dates of 8-K filings with Item 2.02 (Results of Operations) = quarterly earnings releases.
    None if the company is unknown to SEC (e.g. foreign issuers that file 6-K instead)."""
    subs = http.get(SUBMISSIONS_URL.format(cik=cik), as_json=True, ttl=ttl)
    if not subs:
        return None
    filings = subs.get("filings", {})
    s = since.isoformat()
    dates = _earnings_rows(filings.get("recent", {}), s)
    for f in filings.get("files", []) or []:  # older filings live in extra files
        if str(f.get("filingTo", "")) < s or not f.get("name"):
            continue
        try:
            older = http.get(f"https://data.sec.gov/submissions/{f['name']}", as_json=True, ttl=30 * 86400)
        except Exception as e:  # noqa: BLE001
            log.debug("older submissions failed %s: %s", f.get("name"), e)
            continue
        dates += _earnings_rows(older or {}, s)
    forms = filings.get("recent", {}).get("form", [])
    if not dates and not any(f in ("10-Q", "10-K", "8-K") for f in forms):
        return None
    return sorted({date.fromisoformat(d) for d in dates})


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


# ---------------------------------------------------------------- historical data sets (for training)
DATASET_URLS = [
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{q}_form345.zip",
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip",
    "https://www.sec.gov/files/structureddata/data/form-345-data-sets/{q}_form345.zip",
]


def _read_tsv(zf, name: str, usecols: list[str]):
    import pandas as pd

    member = next((n for n in zf.namelist() if n.upper().endswith(name.upper())), None)
    if member is None:
        return pd.DataFrame(columns=usecols)
    wanted = {c.upper() for c in usecols}
    with zf.open(member) as fh:  # read only the needed columns: the quarterly files are large
        df = pd.read_csv(fh, sep="\t", dtype=str, quoting=3, on_bad_lines="skip", encoding="utf-8",
                         encoding_errors="replace", usecols=lambda c: str(c).strip().upper() in wanted)
    df.columns = [c.strip().upper() for c in df.columns]
    for c in usecols:
        if c not in df.columns:
            df[c] = None
    return df[usecols]


def _sec_dates(col):
    """SEC data sets write dates as DD-MON-YYYY (e.g. 15-MAR-2023); accept ISO too."""
    import pandas as pd

    col = col.fillna("").astype(str).str.strip().str.title()
    d = pd.to_datetime(col, format="%d-%b-%Y", errors="coerce")
    iso = pd.to_datetime(col.str[:10], format="%Y-%m-%d", errors="coerce")
    return d.fillna(iso).dt.date.where(d.fillna(iso).notna(), None)


def parse_form345_zip(content: bytes, ciks: set[int] | None = None):
    """Open-market purchases and sales from one quarterly SEC Form 3/4/5 data set, one row per transaction,
    in the same format as flatten_filings() rows (plus 'cik')."""
    import io
    import zipfile

    import pandas as pd

    zf = zipfile.ZipFile(io.BytesIO(content))
    sub = _read_tsv(zf, "SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "AFF10B5ONE"])
    doc = sub["DOCUMENT_TYPE"].fillna("").str.upper().str.replace("FORM", "", regex=False).str.strip()
    sub = sub[doc.isin(["4", "4/A"])].copy()
    sub["CIK"] = pd.to_numeric(sub["ISSUERCIK"], errors="coerce")
    if ciks is not None:
        sub = sub[sub["CIK"].isin(ciks)]
    tr = _read_tsv(zf, "NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                              "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS"])
    tr = tr[tr["TRANS_CODE"].fillna("").str.strip().str.upper().isin(["P", "S"])]
    own = _read_tsv(zf, "REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"])
    own = own.groupby("ACCESSION_NUMBER").agg({"RPTOWNERNAME": "first", "RPTOWNER_RELATIONSHIP": lambda x: ",".join(x.dropna()),
                                               "RPTOWNER_TITLE": lambda x: "; ".join(x.dropna())}).reset_index()
    df = tr.merge(sub, on="ACCESSION_NUMBER").merge(own, on="ACCESSION_NUMBER", how="left")
    if df.empty:
        return pd.DataFrame(columns=["cik", "filing_date", "date", "code", "shares", "price", "ad", "shares_after",
                                     "owner", "title", "is_officer", "is_director", "is_ten_pct", "planned", "top_exec"])
    rel = df["RPTOWNER_RELATIONSHIP"].fillna("").str.upper()
    title = df["RPTOWNER_TITLE"].fillna("")
    out = pd.DataFrame({
        "cik": df["CIK"].astype("Int64"),
        "filing_date": _sec_dates(df["FILING_DATE"]),
        "date": _sec_dates(df["TRANS_DATE"]),
        "code": df["TRANS_CODE"].str.strip().str.upper(),
        "shares": pd.to_numeric(df["TRANS_SHARES"], errors="coerce").fillna(0.0),
        "price": pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce").fillna(0.0),
        "ad": df["TRANS_ACQUIRED_DISP_CD"].fillna("").str.strip().str.upper(),
        "shares_after": pd.to_numeric(df["SHRS_OWND_FOLWNG_TRANS"], errors="coerce"),
        "owner": df["RPTOWNERNAME"].fillna(""),
        "title": title,
        "is_officer": rel.str.contains("OFFICER"),
        "is_director": rel.str.contains("DIRECTOR"),
        "is_ten_pct": rel.str.contains("TENPERCENT"),
        "planned": df["AFF10B5ONE"].fillna("").str.strip().str.upper().isin(["1", "TRUE", "Y", "YES"]),
        "top_exec": title.map(lambda x: bool(CEO_CFO.search(x))).astype(bool),
    })
    return out.dropna(subset=["filing_date"])


def quarters_back(n: int, today: date | None = None) -> list[str]:
    """Labels like '2026q2' for the n most recent COMPLETED quarters (newest first)."""
    d = today or date.today()
    y, q = d.year, (d.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}q{q}")
    return out


def insider_history(http: Http, years: int, ciks: set[int] | None = None, log_fn=None):
    """Open-market insider purchases/sales for the past `years` years from SEC's quarterly data sets."""
    import pandas as pd

    frames = []
    for q in quarters_back(years * 4 + 1):
        content = None
        for pattern in DATASET_URLS:
            try:
                content = http.get_bytes(pattern.format(q=q), cache_name=f"{q}_form345.zip")
            except Exception as e:  # noqa: BLE001
                log.debug("dataset %s failed: %s", q, e)
            if content:
                break
        if not content:
            log.warning("SEC: לא נמצא קובץ נתוני מנהלים לרבעון %s", q)
            continue
        try:
            frames.append(parse_form345_zip(content, ciks))
            if log_fn:
                log_fn(q, len(frames[-1]))
        except Exception as e:  # noqa: BLE001
            log.warning("SEC: קריאת %s נכשלה: %s", q, e)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
