"""Probability model: the chance that a stock rises +30% before its stop is hit, within 3 months.

Plain logistic regression (numpy only, no scipy/sklearn) on the point-in-time features of features.py.
Features are clipped to their 1st-99th percentile, standardised, missing values become the average,
and squares of the continuous features let the model learn 'too much is bad' shapes (e.g. RSI)."""
from __future__ import annotations

import json
import math
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from .features import FEATURES
from .util import SignalResult, clip

BINARY = {"above_50", "trend_50_200", "ins_buy", "ins_top_exec", "ins_net_sell", "earn_recent", "mkt_above_200"}
SQUARED = ["mom_6_1", "rs_63", "ret_21", "log_vol_ratio", "dist_high", "squeeze", "atr_pct", "rsi14",
           "log_dollar_vol", "log_price", "earn_reaction", "mkt_ret_63"]


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float = 1.0, max_iter: int = 60, sample_weight=None) -> np.ndarray:
    """Newton/IRLS with an L2 penalty on everything except the intercept (column 0)."""
    n, d = X.shape
    w = np.zeros(d)
    sw = np.ones(n) if sample_weight is None else sample_weight
    base = float(np.clip(np.average(y, weights=sw), 1e-4, 1 - 1e-4))
    w[0] = math.log(base / (1 - base))
    pen = np.full(d, lam)
    pen[0] = 0.0
    for _ in range(max_iter):
        p = sigmoid(X @ w)
        g = X.T @ (sw * (p - y)) + pen * w
        h = (X * (sw * p * (1 - p))[:, None]).T @ X + np.diag(pen) + 1e-9 * np.eye(d)
        step = np.linalg.solve(h, g)
        w -= step
        if np.max(np.abs(step)) < 1e-7:
            break
    return w


def auc(y: np.ndarray, s: np.ndarray) -> float | None:
    """Area under the ROC curve (Mann-Whitney with average ranks for ties)."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    n1, n0 = y.sum(), len(y) - y.sum()
    if n1 == 0 or n0 == 0:
        return None
    _u, inv, counts = np.unique(s, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(counts) - (counts - 1) / 2.0)[inv]  # average ranks, ties shared
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


class Transform:
    def __init__(self, features: list[str], lo, hi, mean, std):
        self.features, self.lo, self.hi, self.mean, self.std = features, lo, hi, mean, std
        self.sq = [features.index(f) for f in SQUARED if f in features]

    @classmethod
    def fit(cls, raw: np.ndarray, features: list[str]) -> "Transform":
        with warnings.catch_warnings():  # a feature without any data (e.g. no insider history) is fine
            warnings.simplefilter("ignore", RuntimeWarning)
            lo = np.nanpercentile(raw, 1, axis=0)
            hi = np.nanpercentile(raw, 99, axis=0)
        for j, f in enumerate(features):
            if f in BINARY:
                lo[j], hi[j] = 0.0, 1.0
        c = np.clip(raw, lo, hi)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean = np.nanmean(c, axis=0)
            std = np.nanstd(c, axis=0)
        mean = np.where(np.isnan(mean), 0.0, mean)
        std = np.where(~(std > 1e-12), 1.0, std)
        return cls(features, lo, hi, mean, std)

    def __call__(self, raw: np.ndarray) -> np.ndarray:
        raw = np.atleast_2d(np.asarray(raw, dtype=float))
        z = (np.clip(raw, self.lo, self.hi) - self.mean) / self.std
        z = np.where(np.isnan(z), 0.0, z)
        sq = z[:, self.sq] ** 2 - 1.0 if self.sq else np.empty((len(z), 0))
        return np.hstack([np.ones((len(z), 1)), z, sq])

    def to_json(self) -> dict:
        return {"lo": self.lo.tolist(), "hi": self.hi.tolist(), "mean": self.mean.tolist(), "std": self.std.tolist()}


class ProbModel:
    """Fitted model + its out-of-sample report, saved as JSON (also read by the phone app)."""

    def __init__(self, meta: dict):
        self.meta = meta
        f = meta["features"]
        t = meta["transform"]
        self.features = f
        self.tf = Transform(f, *(np.array(t[k], dtype=float) for k in ("lo", "hi", "mean", "std")))
        self.coef = np.array(meta["coef"], dtype=float)
        cal = meta.get("calibration") or {}
        self.cal_a, self.cal_b = float(cal.get("a", 0.0)), float(cal.get("b", 1.0))
        self.base = float(meta.get("base_rate") or 0.0)

    @property
    def quality(self) -> float:
        """0 = failed its out-of-sample test (ignored), 1 = clearly useful (set by train.py)."""
        q = self.meta.get("quality")
        return float(q) if isinstance(q, (int, float)) else 0.0

    def bucket(self, p: float) -> dict | None:
        """What happened in the out-of-sample test to stocks that got a similar probability."""
        dec = (self.meta.get("validation") or {}).get("deciles") or []
        if not dec or p is None:
            return None
        for d in dec:
            if d.get("p_hi") is not None and p <= d["p_hi"]:
                return d
        return dec[-1]

    def raw_matrix(self, rows: list[dict]) -> np.ndarray:
        return np.array([[float(r.get(k, np.nan)) if r.get(k) is not None else np.nan for k in self.features]
                         for r in rows], dtype=float)

    def predict_raw(self, X: np.ndarray) -> np.ndarray:
        p = sigmoid(self.tf(X) @ self.coef)
        return sigmoid(self.cal_a + self.cal_b * logit(p))

    def predict(self, feats: dict) -> float:
        return float(self.predict_raw(self.raw_matrix([feats]))[0])

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_clean(self.meta), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "ProbModel | None":
        try:
            meta = json.loads(Path(path).read_text(encoding="utf-8"))
            if meta.get("features") != FEATURES and not set(meta.get("features", [])) <= set(FEATURES):
                return None
            return cls(meta)
        except (OSError, ValueError, KeyError, TypeError):
            return None


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, (np.floating, np.integer, np.bool_)):
        o = o.item()
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, 6)
    return o


VERSION = 2


def model_spec(settings) -> str:
    """The exact question the model answers. A model is only used when it answers today's question."""
    stop = float(settings.get("risk.atr_stop_multiple"))
    h = int(settings.get("train.horizon_days"))
    if str(settings.get("train.target_mode", "r")).lower() == "pct":
        return f"v{VERSION}:pct{float(settings.get('train.target')):g}:atr{stop:g}:h{h}"
    return f"v{VERSION}:r{float(settings.get('risk.target_r')):g}:atr{stop:g}:h{h}"


def build_meta(features: list[str], tf: Transform, coef: np.ndarray, **extra) -> dict:
    return {"version": VERSION, "features": features, "transform": tf.to_json(), "coef": coef.tolist(),
            "trained": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


# ------------------------------------------------------------------ signal for the daily scan
def probability_signal(p: float | None, model: ProbModel | None) -> SignalResult:
    if p is None or model is None or not model.base:
        return SignalResult("model", 0.0, 0.0)
    base = model.base
    ratio = p / base
    score = clip(math.log(max(ratio, 1e-3)) / math.log(2.5))
    conf = model.quality
    reasons: list[str] = []  # the probability itself is shown on every card, alert and report
    return SignalResult("model", score, conf, reasons, data={"prob": p, "base": base})
