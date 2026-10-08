"""US macro data from FRED (St. Louis Fed). Works without a key via the public CSV endpoint;
uses the official API when FRED_API_KEY is set."""
from __future__ import annotations

import io
import logging

import pandas as pd

from ..net import Http

log = logging.getLogger("radar.fred")

SERIES = {
    "DGS10": "תשואת אג\"ח 10 שנים",
    "DGS2": "תשואת אג\"ח שנתיים",
    "DFF": "ריבית הפד",
    "T10Y2Y": "פער 10 שנים פחות שנתיים",
    "CPIAUCSL": "מדד המחירים לצרכן",
    "UNRATE": "שיעור אבטלה",
    "VIXCLS": "מדד הפחד VIX",
    "BAMLH0A0HYM2": "מרווח אג\"ח זבל",
}

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
API_URL = "https://api.stlouisfed.org/fred/series/observations"


def parse_fred_csv(text: str, series_id: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    if df.empty or df.shape[1] < 2:
        return pd.Series(dtype=float, name=series_id)
    date_col = df.columns[0]
    val_col = series_id if series_id in df.columns else df.columns[1]
    s = pd.Series(pd.to_numeric(df[val_col], errors="coerce").values,
                  index=pd.to_datetime(df[date_col]), name=series_id)
    return s.dropna()


def parse_fred_json(data: dict, series_id: str) -> pd.Series:
    obs = (data or {}).get("observations", [])
    idx, vals = [], []
    for o in obs:
        try:
            vals.append(float(o["value"]))
            idx.append(pd.Timestamp(o["date"]))
        except (KeyError, ValueError):
            continue
    return pd.Series(vals, index=idx, name=series_id)


def fetch_series(http: Http, series_id: str, api_key: str = "") -> pd.Series:
    try:
        if api_key:
            data = http.get(API_URL, params={"series_id": series_id, "api_key": api_key, "file_type": "json",
                                             "observation_start": "2018-01-01"}, as_json=True, ttl=12 * 3600)
            return parse_fred_json(data, series_id)
        text = http.get(CSV_URL, params={"id": series_id, "cosd": "2018-01-01"}, ttl=12 * 3600)
        return parse_fred_csv(text or "", series_id)
    except Exception as e:  # noqa: BLE001
        log.warning("FRED: לא ניתן להוריד %s: %s", series_id, e)
        return pd.Series(dtype=float, name=series_id)


def fetch_all(http: Http, api_key: str = "") -> dict[str, pd.Series]:
    return {sid: fetch_series(http, sid, api_key) for sid in SERIES}
