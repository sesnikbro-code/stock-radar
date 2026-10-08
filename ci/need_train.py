"""Prints 'yes' when the cloud should (re)train the probability model: the model is missing or older than
train.retrain_days, and there was no training attempt in the last 6 days. Standard library only."""
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"


def train_config() -> tuple[bool, int]:
    try:
        text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return True, 30
    m = re.search(r"^train:[^\n]*\n((?:[ \t]+[^\n]*\n?|\s*\n)*)", text, re.M)
    block = m.group(1) if m else ""
    enabled = not re.search(r"^\s+enabled:\s*false", block, re.M | re.I)
    d = re.search(r"^\s+retrain_days:\s*(\d+)", block, re.M)
    return enabled, int(d.group(1)) if d else 30


def main() -> str:
    enabled, retrain_days = train_config()
    if not enabled:
        return "no"
    try:
        trained = datetime.fromisoformat(json.loads((DATA / "model.json").read_text(encoding="utf-8"))["trained"])
        if trained.tzinfo is None:
            trained = trained.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - trained).days < retrain_days:
            return "no"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        last = date.fromisoformat((DATA / "model_attempt.txt").read_text(encoding="utf-8").strip())
        if (date.today() - last).days < 6:
            return "no"
    except (OSError, ValueError):
        pass
    return "yes"


if __name__ == "__main__":
    print(main())
    sys.exit(0)
