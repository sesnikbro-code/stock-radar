"""HTTP client with per-host rate limiting, retries with backoff and a simple disk cache."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger("radar.net")

# Minimum seconds between requests per host (be polite / respect published limits).
HOST_INTERVALS = {
    "www.sec.gov": 0.13,          # SEC: max 10 requests/second
    "data.sec.gov": 0.13,
    "api.gdeltproject.org": 6.0,  # GDELT asks for one request every 5 seconds
    "api.usaspending.gov": 0.5,
    "finnhub.io": 1.1,            # free tier: 60/minute
    "www.imf.org": 0.5,
    "api.worldbank.org": 0.3,
    "fred.stlouisfed.org": 0.3,
    "api.stlouisfed.org": 0.3,
    "api.telegram.org": 0.5,
    "api.callmebot.com": 3.0,
}

FOREVER = -1


class Http:
    def __init__(self, cache_dir: Path, user_agent: str, timeout: int = 30):
        self.cache_dir = Path(cache_dir) / "http"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _throttle(self, host: str) -> None:
        interval = HOST_INTERVALS.get(host, 0.0)
        if interval <= 0:
            return
        with self._locks_guard:
            lock = self._locks[host]
        with lock:
            wait = self._last.get(host, 0.0) + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def _cache_path(self, host: str, key: str) -> Path:
        d = self.cache_dir / host.replace(":", "_")
        d.mkdir(parents=True, exist_ok=True)
        return d / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".txt")

    @staticmethod
    def _fresh(path: Path, ttl: float | None) -> bool:
        if ttl is None or not path.exists():
            return False
        if ttl == FOREVER:
            return True
        return (time.time() - path.stat().st_mtime) < ttl

    # ------------------------------------------------------------------ public
    def request(
        self,
        url: str,
        params: dict | None = None,
        method: str = "GET",
        json_body: dict | None = None,
        headers: dict | None = None,
        ttl: float | None = None,
        as_json: bool = False,
        retries: int = 3,
        cache_404: bool = True,
    ):
        """Return text (or parsed JSON). None on 404. Raises after exhausting retries."""
        host = urlparse(url).netloc
        key = json.dumps([method, url, params or {}, json_body or {}], sort_keys=True, default=str)
        cpath = self._cache_path(host, key)
        if self._fresh(cpath, ttl):
            text = cpath.read_text(encoding="utf-8")
            if text == "__404__":
                return None
            return json.loads(text) if as_json else text

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._throttle(host)
            try:
                r = self.session.request(
                    method, url, params=params, json=json_body, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as e:
                last_err = e
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code == 404:
                if ttl is not None and cache_404:
                    cpath.write_text("__404__", encoding="utf-8")
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = requests.HTTPError(f"{r.status_code} for {url}")
                time.sleep(min(60, 3 * (2 ** attempt)))
                continue
            r.raise_for_status()
            text = r.text
            data = json.loads(text) if as_json else text  # validate before caching
            if ttl is not None:
                cpath.write_text(text, encoding="utf-8")
            return data
        raise last_err if last_err else RuntimeError(f"request failed: {url}")

    def get(self, url: str, **kw):
        return self.request(url, method="GET", **kw)

    def get_bytes(self, url: str, cache_name: str | None = None, timeout: int = 300, retries: int = 3) -> bytes | None:
        """Download a binary file (e.g. a zip), cached forever on disk. None on 404/403."""
        host = urlparse(url).netloc
        d = self.cache_dir / host.replace(":", "_") / "bin"
        d.mkdir(parents=True, exist_ok=True)
        cpath = d / (cache_name or hashlib.sha1(url.encode()).hexdigest())
        if cpath.exists() and cpath.stat().st_size > 0:
            return cpath.read_bytes()
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._throttle(host)
            try:
                r = self.session.get(url, timeout=timeout)
            except requests.RequestException as e:
                last_err = e
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code in (403, 404):
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = requests.HTTPError(f"{r.status_code} for {url}")
                time.sleep(min(60, 3 * (2 ** attempt)))
                continue
            r.raise_for_status()
            cpath.write_bytes(r.content)
            return r.content
        raise last_err if last_err else RuntimeError(f"download failed: {url}")

    def post_json(self, url: str, body: dict, **kw):
        return self.request(url, method="POST", json_body=body, as_json=True, **kw)
