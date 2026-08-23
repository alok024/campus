import hashlib
import re
from datetime import date

from campus.paths import ADMIT


def admit(kind, value):
    if value is None:
        return None
    text = str(value).strip()
    pattern = ADMIT.get(kind)
    if not pattern or not pattern.fullmatch(text):
        return None
    if kind == "isodate":
        try:
            date.fromisoformat(text)
        except ValueError:
            return None
    return text


def admit_int(value, low, high):
    try:
        n = int(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def fingerprint(text):
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().encode()).hexdigest()[:12]
