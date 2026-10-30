"""Price data that cannot be right, fixed before anything is computed from it.

Free price feeds sometimes send one wrong number: an open or a low far away from every other price that day, or a
single close that jumps and comes straight back the next day. One such number can 'hit' a stop that the market never
reached (the paper portfolio sold BBD 31% below its entry on one) or create a breakout that never happened.
Real moves are kept: a gap down that stays down, or a crash that does not come back the next day, is left alone."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger("radar.dataguard")


def clean_price_glitches(panel: dict[str, pd.DataFrame], jump: float = 0.4, back: float = 0.15,
                         far: float = 0.25) -> tuple[dict[str, pd.DataFrame], dict]:
    """Returns the cleaned panel and how many prices were fixed, by kind.

    - a close that moved more than `jump` (40%) and was back within `back` (15%) of the day before on the next day:
      that whole day becomes a flat day at the previous close
    - an open more than `far` (25%) away from both the previous close and the same day's close: the open becomes the
      close of that day
    - a low (high) more than `far` below (above) the day's open, close and the previous close: it becomes the lower
      (higher) of the open and close"""
    if not panel or "Close" not in panel:
        return panel, {}
    C = panel["Close"].astype(float)
    O = panel.get("Open", C).astype(float).copy()
    H = panel.get("High", C).astype(float).copy()
    L = panel.get("Low", C).astype(float).copy()
    prev, nxt = C.shift(1), C.shift(-1)
    r1, r2 = C / prev - 1, nxt / prev - 1
    spike = ((r1.abs() > jump) & (r2.abs() <= back)).fillna(False)
    C = C.mask(spike, prev)
    O, H, L = O.mask(spike, prev), H.mask(spike, prev), L.mask(spike, prev)
    prev = C.shift(1)
    bad_open = (((O / prev - 1).abs() > far) & ((O / C - 1).abs() > far)).fillna(False)
    O = O.mask(bad_open, C)
    body_lo = np.fmin(np.fmin(O, C), prev.fillna(C))
    body_hi = np.fmax(np.fmax(O, C), prev.fillna(C))
    bad_low = (L < body_lo * (1 - far)).fillna(False)
    bad_high = (H > body_hi * (1 + far)).fillna(False)
    L = L.mask(bad_low, np.fmin(O, C))
    H = H.mask(bad_high, np.fmax(O, C))
    counts = {"spike": int(spike.to_numpy().sum()), "open": int(bad_open.to_numpy().sum()),
              "low": int(bad_low.to_numpy().sum()), "high": int(bad_high.to_numpy().sum())}
    if any(counts.values()):
        fixed = (spike | bad_open | bad_low | bad_high)
        names = [c for c in fixed.columns if fixed[c].any()][:12]
        log.info("תוקנו מחירים שגויים: %s (למשל %s)", counts, ", ".join(names))
    out = dict(panel)
    out.update(Close=C, Open=O, High=H, Low=L)
    return out, counts
