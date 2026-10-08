"""Post-earnings drift: stocks that jump on a quarterly report (and beat estimates) tend to keep drifting the same
way for weeks, because investors under-react to news. Uses the features of features.earnings_at()."""
from __future__ import annotations

import math

from ..util import SignalResult, clip, pct


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def earnings_drift_signal(feats: dict, horizon: int = 63) -> SignalResult:
    if not _ok(feats.get("earn_recent")) or feats.get("earn_recent") != 1:
        return SignalResult("earnings_drift", 0.0, 0.0)
    r = feats.get("earn_reaction") if _ok(feats.get("earn_reaction")) else 0.0
    s = feats.get("earn_surprise") if _ok(feats.get("earn_surprise")) else None
    age = feats.get("earn_age") if _ok(feats.get("earn_age")) else 0.0
    parts = [clip(r / 0.08)]
    if s is not None:
        parts.append(clip(s / 0.15))
    fade = 1.0 - 0.6 * clip(age, 0, 1)                 # the effect is strongest in the first weeks
    score = clip(sum(parts) / len(parts) * fade)
    days = int(round(age * horizon))
    when = "בימים האחרונים" if days <= 3 else f"לפני כ־{days} ימי מסחר"
    reasons = []
    if r >= 0.03:
        reasons.append(f"קפצה {pct(r)} מעל השוק בתגובה לדוח הרבעוני {when}. מניות שמגיבות כך לדוח "
                       "נוטות להמשיך לעלות בשבועות שאחרי")
    elif r <= -0.03:
        reasons.append(f"ירדה {pct(-r)} מתחת לשוק בתגובה לדוח הרבעוני {when} – מניות כאלה נוטות להמשיך לחלוש")
    if s is not None and s >= 0.05:
        reasons.append(f"הרווח בדוח האחרון עקף את תחזית האנליסטים ב־{pct(s)}")
    elif s is not None and s <= -0.05:
        reasons.append(f"הרווח בדוח האחרון פספס את תחזית האנליסטים ב־{pct(-s)}")
    conf = 0.9 if s is not None else 0.7
    return SignalResult("earnings_drift", score, conf, reasons, data={"reaction": r, "surprise": s, "days": days})
