"""The stock-ranking models (numpy only, no scipy/sklearn) on the point-in-time features of features.py.

Three kinds compete in train.py and only the one that wins on an earlier period and then also beats the
average on an untouched final period is used:
  hit  - logistic regression: chance to reach the target before the stop (version 6)
  lin  - ridge regression on the trade's result in money, relative to all stocks on the same day
  gbm  - small gradient-boosted decision trees on the same money target (finds combinations, e.g. an insider
         buy that only matters when the trend is up)
For the linear models features are clipped to their 1st-99th percentile, standardised, missing values become the
average, and squares of the continuous features let the model learn 'too much is bad' shapes (e.g. RSI)."""
from __future__ import annotations

import json
import math
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from .features import FEATURES
from .util import SignalResult, clip

BINARY = {"above_50", "trend_50_200", "ins_buy", "ins_top_exec", "ins_net_sell", "ins_first_buy", "earn_recent",
          "mkt_above_200"}
SQUARED = ["mom_6_1", "rs_63", "ret_21", "ret_5", "max_ret_21", "log_vol_ratio", "dist_high", "squeeze", "atr_pct",
           "rsi14", "log_dollar_vol", "log_price", "earn_reaction", "ind_mom", "ind_rs", "mkt_ret_63"]
KIND_HE = {"hit": "מודל היעד (גרסה 6)", "lin": "מודל כסף ליניארי", "gbm": "מודל כסף – עצי החלטה"}


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


def fit_ridge(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """Least squares with an L2 penalty on everything except the intercept (column 0)."""
    d = X.shape[1]
    pen = np.full(d, lam)
    pen[0] = 0.0
    return np.linalg.solve(X.T @ X + np.diag(pen) + 1e-9 * np.eye(d), X.T @ y)


class GBM:
    """Gradient-boosted regression trees (squared error) with histogram splits. Small and conservative on
    purpose: shallow trees, large leaves, slow learning rate and row sampling, because stock returns are noisy."""

    def __init__(self, n_trees: int = 120, depth: int = 3, lr: float = 0.05, min_leaf: int = 300,
                 subsample: float = 0.6, n_bins: int = 32, l2: float = 50.0, seed: int = 7):
        self.n_trees, self.depth, self.lr, self.min_leaf = n_trees, depth, lr, min_leaf
        self.subsample, self.n_bins, self.l2, self.seed = subsample, n_bins, l2, seed
        self.trees: list[dict] = []
        self.base = 0.0
        self.med = None

    # -- fitting
    def _bins(self, X):
        cols = []
        for j in range(X.shape[1]):
            x = X[:, j]
            qs = np.unique(np.quantile(x, np.linspace(0, 1, self.n_bins + 1)[1:-1]))
            cols.append(qs)
        return cols

    def fit(self, X: np.ndarray, y: np.ndarray) -> "GBM":
        X = np.asarray(X, dtype=float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(X, axis=0)
        self.med = np.where(np.isnan(med), 0.0, med)
        Xf = np.where(np.isnan(X), self.med, X)
        self.edges = self._bins(Xf)
        B = np.column_stack([np.searchsorted(e, Xf[:, j], side="right") for j, e in enumerate(self.edges)]).astype(np.int32)
        n = len(y)
        self.base = float(np.mean(y))
        pred = np.full(n, self.base)
        rng = np.random.default_rng(self.seed)
        n_nodes = 2 ** self.depth - 1
        for _ in range(self.n_trees):
            idx = np.flatnonzero(rng.random(n) < self.subsample)
            res = y[idx] - pred[idx]
            feat = np.full(n_nodes, -1, dtype=np.int32)
            tbin = np.full(n_nodes, 10 ** 6, dtype=np.int64)
            node = np.zeros(len(idx), dtype=np.int64)
            Bi = B[idx]
            for level in range(self.depth):
                for nd in range(2 ** level - 1, 2 ** (level + 1) - 1):
                    m = node == nd
                    cnt = int(m.sum())
                    if cnt < 2 * self.min_leaf:
                        continue
                    r = res[m]
                    S, N = r.sum(), cnt
                    best = (0.0, -1, -1)
                    for j in range(Bi.shape[1]):
                        nb = len(self.edges[j]) + 1
                        if nb < 2:
                            continue
                        b = Bi[m, j]
                        hs = np.bincount(b, weights=r, minlength=nb)
                        hc = np.bincount(b, minlength=nb)
                        cs, cc = np.cumsum(hs)[:-1], np.cumsum(hc)[:-1]
                        ok = (cc >= self.min_leaf) & (N - cc >= self.min_leaf)
                        if not ok.any():
                            continue
                        gain = cs ** 2 / (cc + self.l2) + (S - cs) ** 2 / (N - cc + self.l2) - S ** 2 / (N + self.l2)
                        gain = np.where(ok, gain, -np.inf)
                        k = int(np.argmax(gain))
                        if gain[k] > best[0]:
                            best = (float(gain[k]), j, k)
                    if best[1] >= 0:
                        feat[nd], tbin[nd] = best[1], best[2]
                go_right = np.zeros(len(idx), bool)
                cur = node.copy()
                f_ = feat[cur]
                has = f_ >= 0
                go_right[has] = Bi[np.flatnonzero(has), f_[has]] > tbin[cur[has]]
                node = 2 * node + 1 + go_right
            leaf_idx = node - n_nodes
            sums = np.bincount(leaf_idx, weights=res, minlength=n_nodes + 1)
            cnts = np.bincount(leaf_idx, minlength=n_nodes + 1)
            leaf = self.lr * sums / (cnts + self.l2)
            thr = np.array([self.edges[f][t] if f >= 0 and t < len(self.edges[f]) else np.inf
                            for f, t in zip(feat, tbin)])
            tree = {"feat": feat, "thr": thr, "leaf": leaf}
            self.trees.append(tree)
            pred += self._tree_predict(tree, Xf)
        return self

    # -- prediction
    def _tree_predict(self, tree: dict, Xf: np.ndarray) -> np.ndarray:
        n = len(Xf)
        node = np.zeros(n, dtype=np.int64)
        feat, thr = np.asarray(tree["feat"]), np.asarray(tree["thr"], dtype=float)
        rows = np.arange(n)
        for _ in range(self.depth):
            f_ = feat[node]
            x = Xf[rows, np.maximum(f_, 0)]
            right = (f_ >= 0) & (x >= thr[node])
            node = 2 * node + 1 + right
        return np.asarray(tree["leaf"], dtype=float)[node - (2 ** self.depth - 1)]

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        Xf = np.where(np.isnan(X), self.med, X)
        out = np.full(len(Xf), self.base)
        for t in self.trees:
            out += self._tree_predict(t, Xf)
        return out

    def to_json(self) -> dict:
        big = 1e300
        return {"depth": self.depth, "base": self.base, "med": self.med.tolist(),
                "trees": [{"feat": t["feat"].tolist(), "thr": [big if not np.isfinite(v) else float(v) for v in t["thr"]],
                           "leaf": np.asarray(t["leaf"]).tolist()} for t in self.trees]}

    @classmethod
    def from_json(cls, d: dict) -> "GBM":
        g = cls(depth=int(d["depth"]))
        g.base, g.med = float(d["base"]), np.array(d["med"], dtype=float)
        g.trees = [{"feat": np.array(t["feat"], dtype=np.int32), "thr": np.array(t["thr"], dtype=float),
                    "leaf": np.array(t["leaf"], dtype=float)} for t in d["trees"]]
        return g


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
    """The chosen model + its out-of-sample report, saved as JSON (also read by the phone app)."""

    def __init__(self, meta: dict):
        self.meta = meta
        self.kind = meta.get("kind") or "hit"
        self.features = meta["features"]
        t = meta.get("transform")
        self.tf = Transform(self.features, *(np.array(t[k], dtype=float) for k in ("lo", "hi", "mean", "std"))) if t else None
        self.coef = np.array(meta["coef"], dtype=float) if meta.get("coef") is not None else None
        self.gbm = GBM.from_json(meta["gbm"]) if meta.get("gbm") else None
        cal = meta.get("calibration") or {}
        self.cal_a, self.cal_b = float(cal.get("a", 0.0)), float(cal.get("b", 1.0))
        self.base = float(meta.get("base_rate") or 0.0)

    @property
    def quality(self) -> float:
        """0 = failed its out-of-sample test (ignored), 1 = clearly useful (set by train.py)."""
        q = self.meta.get("quality")
        return float(q) if isinstance(q, (int, float)) else 0.0

    @property
    def base_ret(self) -> float | None:
        a = ((self.meta.get("validation") or {}).get("money_all") or {})
        return a.get("ret")

    def bucket(self, score: float) -> dict | None:
        """What happened in the out-of-sample test to stocks that got a similar score."""
        dec = (self.meta.get("validation") or {}).get("deciles") or []
        if not dec or score is None:
            return None
        for d in dec:
            if d.get("p_hi") is not None and score <= d["p_hi"]:
                return d
        return dec[-1]

    def raw_matrix(self, rows: list[dict]) -> np.ndarray:
        return np.array([[float(r.get(k, np.nan)) if r.get(k) is not None else np.nan for k in self.features]
                         for r in rows], dtype=float)

    def score_raw(self, X: np.ndarray) -> np.ndarray:
        if self.kind == "gbm":
            return self.gbm.predict(X)
        z = self.tf(X) @ self.coef
        if self.kind == "lin":
            return z
        return sigmoid(self.cal_a + self.cal_b * logit(sigmoid(z)))

    def score(self, feats: dict) -> float:
        return float(self.score_raw(self.raw_matrix([feats]))[0])

    def display_prob(self, score: float, hist: dict | None = None) -> float | None:
        """Chance to reach the target before the stop: the calibrated probability of the hit model, or what
        happened in the test to stocks with a similar score for the money models."""
        if self.kind == "hit":
            return score
        hist = hist if hist is not None else self.bucket(score)
        return hist.get("rate") if hist else None

    def predict(self, feats: dict) -> float:  # kept for older callers: the displayed probability
        s = self.score(feats)
        return self.display_prob(s)

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
            if not set(meta.get("features", [])) <= set(FEATURES):
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


VERSION = 3


def model_spec(settings) -> str:
    """The exact question the model answers. A model is only used when it answers today's question."""
    stop = float(settings.get("risk.atr_stop_multiple"))
    h = int(settings.get("train.horizon_days"))
    if str(settings.get("train.target_mode", "r")).lower() == "pct":
        return f"v{VERSION}:pct{float(settings.get('train.target')):g}:atr{stop:g}:h{h}"
    return f"v{VERSION}:r{float(settings.get('risk.target_r')):g}:atr{stop:g}:h{h}"


def build_meta(features: list[str], tf: Transform | None, coef: np.ndarray | None, **extra) -> dict:
    return {"version": VERSION, "features": features, "transform": tf.to_json() if tf is not None else None,
            "coef": coef.tolist() if coef is not None else None,
            "trained": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


# ------------------------------------------------------------------ signal for the daily scan
def probability_signal(score: float | None, model: ProbModel | None, hist: dict | None = None) -> SignalResult:
    """Model signal for the upside score: how much better trades with a similar score did in the out-of-sample
    test than the average stock (+3% per trade or more = full score)."""
    if score is None or model is None:
        return SignalResult("model", 0.0, 0.0)
    hist = hist if hist is not None else model.bucket(score)
    base = model.base_ret
    if not hist or hist.get("ret") is None or base is None:
        return SignalResult("model", 0.0, 0.0)
    edge = hist["ret"] - base
    reasons: list[str] = []  # the probability and the historical result are shown on every card
    return SignalResult("model", clip(edge / 0.03), model.quality, reasons,
                        data={"score": score, "edge": edge, "prob": model.display_prob(score, hist)})
