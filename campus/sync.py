import os
import shutil
import signal
import threading
import time
from datetime import datetime

from campus.autostart import autostart_disable
from campus.browser import Browser, close_active
from campus.google import GOOGLE_SCOPES, _gcal_delete_calendar, calendar_sync, gmail_scan, google_disable
from campus.ics import write_ics
from campus.paths import FAILS, HOME, ICS, LOCK, PIDFILE, STATE, TASKS, UmsError, _SYNC_LOCK
from campus.storage import load, save
from campus.telegram import notify
from campus.ums import changes, login, read_all


def sync_once(user, password):
    with Browser() as br:
        login(br, user, password)
        return read_all(br)


def _merge_forward(old, new):
    if old:
        for k in ("attendance", "messages", "marked"):
            if not new.get(k) and old.get(k):
                new[k] = old[k]
    return new


def _record_failure(exc):
    n = load(FAILS, {"count": 0}).get("count", 0) + 1
    save(FAILS, {"count": n})
    if n == 3 or (n > 3 and n % 32 == 0):
        notify("campus can't log in", f"UMS sync has failed {n} times in a row: {exc}\n"
               "Could be a changed password, a UMS/network outage, or a portal change — "
               "if it keeps failing, run campus.py and re-enter your details.")


def _record_success():
    FAILS.unlink(missing_ok=True)


def check(user, password):
    if not _SYNC_LOCK.acquire(blocking=False):
        print("another campus sync is already in progress — skipping this cycle.")
        return []
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 300:
            print("another campus sync is already in progress — skipping this cycle.")
            return []
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
        return _check_locked(user, password)
    finally:
        _SYNC_LOCK.release()


def _check_locked(user, password):
    from campus.google import _google_token_path
    from campus.reminders import due_reminders

    try:
        old = load(STATE, None)
        new = _merge_forward(old, sync_once(user, password))
        diff = changes(old, new)
        save(STATE, new)
        tasks = load(TASKS, [])
        write_ics(new, tasks)
        calendar_on = bool(load(_google_token_path("calendar"), None))
        if diff:
            body = "\n".join(f"- {c}" for c in diff[:8])
            if not calendar_on:
                body += "\n\n(re-import campus.ics into Google Calendar to update it there too)"
            notify("UMS changed", body)
        for t in due_reminders():
            notify("Reminder", f"{t['text']} — due {t['when']}")
        if calendar_on:
            try:
                calendar_sync(old, new, tasks)
            except Exception as exc:
                print(f"calendar sync skipped: {exc}")
        if load(_google_token_path("gmail"), None):
            try:
                important_mail = gmail_scan()
                if important_mail:
                    notify("Important mail", "\n".join(f"- {s}" for s in important_mail[:8]))
            except Exception as exc:
                print(f"gmail scan skipped: {exc}")
        return diff
    finally:
        LOCK.unlink(missing_ok=True)


def run_once(credentials_fn, setup_telegram_fn):
    user, password = credentials_fn()
    setup_telegram_fn()
    print("checking UMS…")
    diff = check(user, password)
    if not diff:
        print("no change")
    elif os.environ.get("CAMPUS_QUIET"):
        print(f"{len(diff)} change(s) — sent to your notifications")
    else:
        print("changes:\n" + "\n".join(f"- {c}" for c in diff))


def run_loop(credentials_fn, setup_telegram_fn, telegram_listen_fn):
    from campus.paths import INTERVAL_MIN

    print(f"campus is watching UMS every {INTERVAL_MIN} min. Ctrl+C to stop.")
    print(f"calendar file kept fresh at: {ICS}")
    user, password = credentials_fn()
    setup_telegram_fn()
    HOME.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    stop_event = threading.Event()
    threading.Thread(target=telegram_listen_fn, args=(stop_event, user, password), daemon=True).start()
    try:
        while True:
            stamp = datetime.now().strftime("%H:%M")
            try:
                diff = check(user, password)
                print(f"[{stamp}] {len(diff)} change(s):\n" + "\n".join(f"- {c}" for c in diff)
                      if diff else f"[{stamp}] no change")
                _record_success()
            except KeyboardInterrupt:
                print("\nstopped.")
                return
            except UmsError as exc:
                print(f"[{stamp}] sync skipped: {exc}")
                _record_failure(exc)
            except Exception as exc:
                print(f"[{stamp}] sync hit an unexpected error: {exc!r}")
                _record_failure(exc)
            try:
                if stop_event.wait(INTERVAL_MIN * 60):
                    return
            except KeyboardInterrupt:
                print("\nstopped.")
                return
    finally:
        stop_event.set()
        PIDFILE.unlink(missing_ok=True)


def _bomb_now():
    from campus.google import _google_token_path

    try:
        if load(_google_token_path("calendar"), None):
            try:
                _gcal_delete_calendar()
            except Exception:
                pass
        for module in GOOGLE_SCOPES:
            try:
                google_disable(module)
            except Exception:
                pass
        autostart_disable()
        if PIDFILE.exists():
            try:
                pid = int(PIDFILE.read_text(encoding="utf-8").strip())
                if pid != os.getpid():
                    os.kill(pid, signal.SIGTERM)
            except (OSError, ValueError):
                pass
    finally:
        close_active()
        if HOME.exists():
            shutil.rmtree(HOME, ignore_errors=True)
