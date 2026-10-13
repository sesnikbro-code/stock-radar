"""Prints 'yes' when the cloud should (re)train the probability model:
  - there is no model, or it answers a different question than config.yaml now asks (e.g. after an update), or
  - it is older than train.retrain_days, or an update added new checks to the training (TRAIN_REV), or
  - the self-review asked for it (the model picks worse live than in its test, or a suspected lesson waits for
    its check on history) and the model is at least a week old,
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


def train_rev() -> int:
    """What the current training produces (an older model is retrained once after an update)."""
    try:
        sys.path.insert(0, str(ROOT))
        from radar.train import TRAIN_REV
        return int(TRAIN_REV)
    except Exception:  # noqa: BLE001
        m = re.search(r"^TRAIN_REV\s*=\s*(\d+)", (ROOT / "radar" / "train.py").read_text(encoding="utf-8"), re.M) \
            if (ROOT / "radar" / "train.py").exists() else None
        return int(m.group(1)) if m else 1


def review_request(trained: datetime) -> bool:
    """The self-review asked for an early retraining after this model was trained."""
    try:
        req = json.loads((DATA / "review.json").read_text(encoding="utf-8")).get("retrain_request") or {}
        asked = date.fromisoformat(str(req.get("date"))[:10])
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    age = (datetime.now(timezone.utc) - trained).days
    return bool(req.get("reasons")) and asked >= trained.date() and age >= 7


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
        if fresh and int(model.get("train_rev") or 1) < train_rev():   # an update added new checks to the training
            fresh = False
        if fresh and review_request(trained):
            fresh = False
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if fresh:
        return "no"
    try:
        parts = (DATA / "model_attempt.txt").read_text(encoding="utf-8").split()
        last = date.fromisoformat(parts[0])
        attempted = parts[1] if len(parts) > 1 else None
        # the same question with the same training was already tried this week (a failing training must not loop)
        if (date.today() - last).days < 6 and (spec is None or attempted == attempt_key(spec)):
            return "no"
    except (OSError, ValueError, IndexError):
        pass
    return "yes"


def attempt_key(spec: str | None) -> str:
    """The question (spec) plus the training revision: an update that changes the training is a new attempt."""
    return f"{spec or ''}#r{train_rev()}"


def current_spec() -> str:
    return attempt_key(train_config()[2])


if __name__ == "__main__":
    print(current_spec() if sys.argv[1:] == ["--spec"] else main())
    sys.exit(0)
