"""Optional: classify news headlines with Claude (needs ANTHROPIC_API_KEY). Returns per-headline
sentiment, impact, event type and a short Hebrew summary. Falls back to the keyword engine on failure."""
from __future__ import annotations

import json
import logging
import re

import requests

log = logging.getLogger("radar.llm")

API_URL = "https://api.anthropic.com/v1/messages"

PROMPT = """You are an equity news analyst. For the company {ticker} ({name}), classify each headline below.
Return ONLY a JSON array, one object per headline, in the same order, with keys:
"sentiment": number from -1 (very bad for the stock) to 1 (very good),
"impact": number from 0 (noise) to 1 (likely to move the stock a lot),
"event": one of ["earnings","guidance","analyst","contract","regulatory","clinical","mna","dilution","legal","distress","management","macro","other"],
"he": a short Hebrew summary of the headline (max 12 words).

Headlines:
{lines}"""


def classify_headlines(api_key: str, model: str, ticker: str, name: str, news: list[dict], timeout: int = 60) -> list[dict] | None:
    if not api_key or not news:
        return None
    items = news[:15]
    lines = "\n".join(f"{i + 1}. {n['title']}" for i, n in enumerate(items))
    body = {
        "model": model,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": PROMPT.format(ticker=ticker, name=name, lines=lines)}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        r = requests.post(API_URL, json=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        text = "".join(c.get("text", "") for c in r.json().get("content", []) if c.get("type") == "text")
        m = re.search(r"\[.*\]", text, re.S)
        parsed = json.loads(m.group(0)) if m else None
    except Exception as e:  # noqa: BLE001
        log.warning("סיווג חדשות עם Claude נכשל עבור %s: %s", ticker, e)
        return None
    if not isinstance(parsed, list) or len(parsed) != len(items):
        return None
    out = []
    for n, p in zip(items, parsed):
        try:
            out.append({**n, "llm_sentiment": max(-1.0, min(1.0, float(p.get("sentiment", 0)))),
                        "llm_impact": max(0.0, min(1.0, float(p.get("impact", 0.5)))),
                        "llm_event": str(p.get("event", "other")), "he": str(p.get("he", ""))})
        except (TypeError, ValueError, AttributeError):
            out.append(n)
    return out
