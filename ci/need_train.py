"""Prints 'yes' when the cloud should (re)train the probability model:
  - there is no model, or it answers a different question than config.yaml now asks (e.g. after an update), or
  - it is older than train.retrain_days,
and the same question was not already attempted in the last 6 days (so a failing training does not loop)."""
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"


def train_config() -> tuple[bool, int, str | None]:
    """(enabled, retrain_days, spec). spec is None if the app code can't be imported (then only age counts)."""
    try:
        sys.path.insert(0, str(ROOT))
        from radar.model import model_spec
        from radar.settings import load_settings

        s = load_settings(root=ROOT)
        return bool(s.get("train.enabled", True)), int(s.get("train.retrain_days", 30)), model_spec(s)
    except Exception:  # noqa: BLE001 - fall back to reading config.yaml as text
        pass
    try:
        text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return True, 30, None
    m = re.search(r"^train:[^\n]*\n((?:[ \t]+[^\n]*\n?|\s*\n)*)", text, re.M)
    block = m.group(1) if m else ""
    enabled = not re.search(r"^\s+enabled:\s*false", block, re.M | re.I)
    d = re.search(r"^\s+retrain_days:\s*(\d+)", block, re.M)
    return enabled, int(d.group(1)) if d else 30, None


def main() -> str:
    enabled, retrain_days, spec = train_config()
    if not enabled:
        return "no"
    fresh = False
    try:
        model = json.loads((DATA / "model.json").read_text(encoding="utf-8"))
        trained = datetime.fromisoformat(model["trained"])
        if trained.tzinfo is None:
            trained = trained.replace(tzinfo=timezone.utc)
        same_question = spec is None or model.get("spec") == spec
        fresh = same_question and (datetime.now(timezone.utc) - trained).days < retrain_days
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if fresh:
        return "no"
    try:
        parts = (DATA / "model_attempt.txt").read_text(encoding="utf-8").split()
        last = date.fromisoformat(parts[0])
        attempted_spec = parts[1] if len(parts) > 1 else None
        if (date.today() - last).days < 6 and (spec is None or attempted_spec == spec):
            return "no"
    except (OSError, ValueError, IndexError):
        pass
    return "yes"


def current_spec() -> str:
    return train_config()[2] or ""


if __name__ == "__main__":
    print(current_spec() if sys.argv[1:] == ["--spec"] else main())
    sys.exit(0)
