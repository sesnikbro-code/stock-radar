"""Regression tests for HTTP caching and retry behavior."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from radar.net import Http


class TestHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.http = Http(Path(self.tmp.name), "StockRadar tests", timeout=1)

    def test_cache_is_separated_by_headers(self):
        response_a, response_b = Mock(), Mock()
        response_a.status_code, response_a.text = 200, '{"value":"A"}'
        response_b.status_code, response_b.text = 200, '{"value":"B"}'
        response_a.raise_for_status.return_value = None
        response_b.raise_for_status.return_value = None
        with patch.object(self.http.session, "request", side_effect=[response_a, response_b]) as req:
            a = self.http.get("https://example.test/data", ttl=60, as_json=True, headers={"X-Mode": "A"})
            b = self.http.get("https://example.test/data", ttl=60, as_json=True, headers={"X-Mode": "B"})
        self.assertEqual(a["value"], "A")
        self.assertEqual(b["value"], "B")
        self.assertEqual(req.call_count, 2)

    def test_retry_after_is_honored_with_cap(self):
        throttled, ok = Mock(), Mock()
        throttled.status_code, throttled.headers = 429, {"Retry-After": "2"}
        ok.status_code, ok.text = 200, "ok"
        ok.raise_for_status.return_value = None
        with patch.object(self.http.session, "request", side_effect=[throttled, ok]), patch("radar.net.time.sleep") as sleep:
            result = self.http.get("https://example.test/data", retries=1)
        self.assertEqual(result, "ok")
        sleep.assert_called_once_with(2.0)

    def test_invalid_retry_after_uses_backoff(self):
        throttled, ok = Mock(), Mock()
        throttled.status_code, throttled.headers = 503, {"Retry-After": "later"}
        ok.status_code, ok.text = 200, "ok"
        ok.raise_for_status.return_value = None
        with patch.object(self.http.session, "request", side_effect=[throttled, ok]), patch("radar.net.time.sleep") as sleep:
            self.http.get("https://example.test/data", retries=1)
        sleep.assert_called_once_with(3)

if __name__ == "__main__":
    unittest.main()
