"""Configuration loading: config.yaml merged over built-in defaults, secrets from .env / environment."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "general": {
        "sec_email": "",
        "data_dir": "data",
        "reports_dir": "reports",
        "log_level": "INFO",
    },
    "universe": {
        "source": "sec",              # sec | list
        "exchanges": ["Nasdaq", "NYSE"],
        "max_tickers": 0,             # 0 = no limit
        "watchlist": [],
        "list": [],                   # used when source == list
        "extra_tickers_file": "",
    },
    "filters": {
        "min_price": 2.0,
        "min_dollar_volume": 5_000_000,
        "min_market_cap": 200_000_000,
        "min_history_days": 120,
    },
    "scan": {
        "top_candidates": 100,
        "max_picks": 10,
        "min_upside_score": 60,
        "max_risk_score": 80,
        "history_period": "2y",
        "workers": 4,
    },
    "insider": {
        "enabled": True,
        "lookback_days": 90,
        "discovery_enabled": True,
        "discovery_days": 3,
        "discovery_max_filings": 1500,
        "cluster_min_insiders": 2,
        "cluster_min_value": 250_000,
    },
    "analysts": {"enabled": True, "accuracy_horizon_days": 60},
    "news": {
        "enabled": True,
        "lookback_days": 7,
        "use_finnhub": True,
        "use_llm": False,
        "llm_model": "claude-haiku-5-5",
        "show_original_headlines": True,
    },
    "options": {"enabled": True, "max_expirations": 2},
    "gov_contracts": {"enabled": True, "lookback_days": 120, "max_market_cap": 30_000_000_000},
    "geopolitics": {"enabled": True},
    "macro": {"enabled": True},
    "countries": {"enabled": True, "max_countries": 8, "check_conflict": True},
    "weights": {
        "momentum": 1.0,
        "volume_accumulation": 0.8,
        "breakout_setup": 0.7,
        "insider_buying": 1.5,
        "analyst_revisions": 1.3,
        "analyst_ratings": 0.6,
        "price_target": 0.4,
        "short_squeeze": 0.8,
        "options_flow": 0.8,
        "news": 0.7,
        "catalysts": 0.8,
        "gov_contracts": 0.7,
        "geopolitics": 0.5,
        "macro": 0.5,
        "country": 0.4,
    },
    "risk": {
        "account_size": 50_000,
        "risk_per_trade_pct": 1.0,
        "atr_stop_multiple": 2.5,
        "max_position_pct": 10.0,
        "earnings_blackout_days": 5,
    },
    "paper": {
        "enabled": True,
        "max_open_positions": 15,
        "max_hold_days": 90,
        "trailing_stop": True,
    },
    "learning": {
        "enabled": True,
        "horizon_days": 30,
        "min_samples": 60,
        "gain": 5.0,
        "min_factor": 0.25,
        "max_factor": 2.0,
    },
    "backtest": {
        "years": 5,
        "rebalance_days": 21,
        "top_n": 10,
        "hold_days": 21,
        "cost_pct": 0.2,
        "use_stops": True,
        "explosive_threshold": 0.30,
        "explosive_window_days": 63,
        "max_tickers": 1500,
    },
    "alerts": {"max_items": 10, "send_when_empty": True},
}

SECRET_KEYS = [
    "SEC_EMAIL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "CALLMEBOT_PHONE",
    "CALLMEBOT_APIKEY",
    "FINNHUB_API_KEY",
    "FRED_API_KEY",
    "ANTHROPIC_API_KEY",
]


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


class Settings:
    def __init__(self, cfg: dict, root: Path):
        self.cfg = cfg
        self.root = root
        self.secrets = {k: os.environ.get(k, "").strip() for k in SECRET_KEYS}
        if self.secrets["SEC_EMAIL"]:
            self.cfg["general"]["sec_email"] = self.secrets["SEC_EMAIL"]

    def __getitem__(self, key):
        return self.cfg[key]

    def get(self, dotted: str, default=None):
        node = self.cfg
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def secret(self, key: str) -> str:
        return self.secrets.get(key, "")

    @property
    def data_dir(self) -> Path:
        p = self.root / self.cfg["general"]["data_dir"]
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_dir(self) -> Path:
        p = self.data_dir / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def reports_dir(self) -> Path:
        p = self.root / self.cfg["general"]["reports_dir"]
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def user_agent(self) -> str:
        email = self.cfg["general"].get("sec_email") or "anonymous@example.com"
        return f"StockRadar/1.0 ({email})"


def load_settings(config_path: str | Path | None = None, root: Path | None = None) -> Settings:
    root = Path(root) if root else ROOT
    load_dotenv(root / ".env")
    path = Path(config_path) if config_path else root / "config.yaml"
    user_cfg = {}
    if path.exists():
        user_cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = _deep_merge(DEFAULTS, user_cfg)
    return Settings(cfg, root)
