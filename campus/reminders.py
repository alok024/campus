from datetime import datetime, timedelta

from campus.ics import write_ics
from campus.paths import INTERVAL_MIN, STATE, TASKS, _TASKS_LOCK
from campus.storage import load, save
from campus.util import fingerprint


def add_reminder(text, when_iso):
    with _TASKS_LOCK:
        tasks = load(TASKS, [])
        tasks.append({"id": fingerprint(text + when_iso)[:8], "text": text, "when": when_iso,
                      "notified": False})
        save(TASKS, tasks)
    print(f"got it — will remind you before {when_iso}: {text}")
    snap = load(STATE, None)
    if snap:
        write_ics(snap, tasks)


def due_reminders():
    with _TASKS_LOCK:
        tasks = load(TASKS, [])
        now = datetime.now()
        fired, changed = [], False
        for t in tasks:
            if t.get("notified"):
                continue
            try:
                when = datetime.fromisoformat(t["when"])
            except ValueError:
                continue
            if when - timedelta(minutes=INTERVAL_MIN + 15) <= now:
                fired.append(t)
                t["notified"] = True
                changed = True
        if changed:
            save(TASKS, tasks)
        return fired


def _parse_reminder(args):
    for take in (2, 1):
        if len(args) - take < 1:
            continue
        candidate = " ".join(args[-take:]).strip()
        norm = candidate.replace(" ", "T", 1) if "T" not in candidate else candidate
        try:
            when = datetime.fromisoformat(norm)
        except ValueError:
            continue
        text = " ".join(args[:-take]).strip()
        if text:
            return text, when.isoformat(timespec="minutes")
    return None, None
