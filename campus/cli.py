"""campus — an agent that watches LPU UMS so you don't have to.

It logs into UMS every 45 minutes, notices when a class is cancelled or moved or a
new notice is posted, tells you, keeps a calendar file up to date, and reminds you
about things you ask it to remember. Everything runs on your own machine.

Run it:            python campus.py
Add a reminder:    python campus.py remind "submit DBMS assignment" 2026-08-25 17:00
List reminders:    python campus.py reminders
Sync once and exit:python campus.py once
Start on login:    python campus.py autostart
Stop that:         python campus.py autostart off
Connect Google:    python campus.py enable calendar   (or: gmail)
Disconnect:        python campus.py disable calendar  (or: gmail)
What's connected:  python campus.py permissions
Delete everything: python campus.py bomb

Once a Telegram bot is connected, campus also listens for commands from your phone —
/status /sync /remind /reminders /disable /autostart /bomb /help — while the loop runs.

Needs: Python 3.9+, Google Chrome or Microsoft Edge, and one package:
    pip install -r requirements.txt
"""

import sys

from campus.autostart import autostart_disable, autostart_enable
from campus.bot import telegram_listen
from campus.google import google_disable, google_enable, google_permissions
from campus.paths import LOG, TASKS, UmsError
from campus.storage import ask, credentials, load, redirect_to_log_if_headless
from campus.reminders import _parse_reminder, add_reminder
from campus.sync import _bomb_now, run_loop, run_once
from campus.telegram import setup_telegram, telegram_chat_id


def _credentials():
    return credentials(autostart_enable)


def usage():
    print(__doc__.strip())


def bomb():
    from campus.paths import HOME

    print("This deletes EVERYTHING campus stored: your saved login, Telegram token,")
    print("the calendar file, reminders, the browser profile, and any Google Calendar /")
    print(f"Gmail connection — all of {HOME}.")
    if ask('Type DELETE to confirm: ').strip() != "DELETE":
        print("cancelled.")
        return
    _bomb_now()
    print("done — campus wiped itself. Delete campus.py too if you're finished with it.")


def main(argv):
    try:
        import websocket
        websocket.create_connection
    except (ImportError, AttributeError):
        print("campus needs one package that isn't installed (or a different 'websocket'")
        print("package is shadowing it): websocket-client")
        print("try:   pip install -r requirements.txt")
        print("if that says 'externally-managed-environment', try:")
        print("       pip install --break-system-packages -r requirements.txt")
        return 1
    if not argv:
        redirect_to_log_if_headless(LOG)
        run_loop(_credentials, setup_telegram, telegram_listen)
        return 0
    cmd = argv[0]
    if cmd == "once":
        run_once(_credentials, setup_telegram)
    elif cmd in ("remind", "add") and len(argv) >= 3:
        text, when_iso = _parse_reminder(argv[1:])
        if not text:
            print('use:  python campus.py remind "what to do" "2026-08-25 17:00"')
            return 2
        add_reminder(text, when_iso)
    elif cmd == "reminders":
        for t in load(TASKS, []):
            mark = "done" if t.get("notified") else "pending"
            print(f"{t['when']}  [{mark}]  {t['text']}")
    elif cmd == "chatid" and len(argv) >= 2:
        print(telegram_chat_id(argv[1]) or "no message found — send your bot 'hi' first, then retry")
    elif cmd == "enable" and len(argv) >= 2:
        google_enable(argv[1].lower())
    elif cmd == "disable" and len(argv) >= 2:
        google_disable(argv[1].lower())
    elif cmd == "permissions":
        google_permissions()
    elif cmd == "autostart":
        arg = argv[1].lower() if len(argv) >= 2 else None
        if arg is None:
            autostart_enable()
        elif arg == "off":
            autostart_disable()
        else:
            print('use:  python campus.py autostart        (turn on)')
            print('  or  python campus.py autostart off    (turn off)')
            return 2
    elif cmd == "bomb":
        bomb()
    else:
        usage()
    return 0


def run(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        raise SystemExit(main(argv))
    except UmsError as exc:
        print(f"campus: {exc}")
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
