# campus

LPU's UMS and LPU Touch never tell you when a class is cancelled or moved. **campus** watches
UMS for you and tells you — desktop pop-up, Telegram, and your calendar — the moment something
changes. One Python file. Runs on your own machine. No server, no account, no website.

## What it provides

- **Change alerts** — cancelled/moved classes and new UMS notices, e.g. `CANCELLED: Wed 10:20
  CAP7001`, `MOVED: Mon 12:00 CAB106: 36-603 -> 38-610`. Checked every 45 minutes (UMS has no
  live feed, so this isn't instant, but it's fast enough to catch a class before it starts).
- **Calendar** — writes `campus.ics` (classes, exams, holidays) kept current automatically;
  import it into Google Calendar and re-imports update events in place, no duplicates.
- **Reminders** — tell it what to do and when; it notifies you and puts it on the calendar.
- **Phone alerts** — connect a Telegram bot and every alert reaches your phone, screen off or not.
- **Autostart** — one command and it starts itself every time you log in, no terminal needed.

## Install

Needs **Python 3.9+** and **Google Chrome or Microsoft Edge** (UMS's bot-check requires a real
browser — you'll see it flash open briefly on each check, that's expected, not a virus).

```
pip install websocket-client
curl -O https://raw.githubusercontent.com/alok024/campus/main/campus.py
```

If `pip install` fails with `externally-managed-environment` (common on Linux/Mac):
```
pip install --break-system-packages websocket-client
```

Headless Linux server (no display): also `sudo apt install xvfb`.

## Commands

| Command | What it does |
|---|---|
| `python campus.py` | Starts the watch loop (checks every 45 min). First run asks for your UMS login, whether to save it, and whether to autostart. |
| `python campus.py once` | Checks right now, then exits. |
| `python campus.py remind "text" "2026-08-25 17:00"` | Adds a personal reminder — fires a notification and appears on your calendar at that time. |
| `python campus.py reminders` | Lists reminders you've set. |
| `python campus.py autostart` | Makes campus start itself at every login. Needs a saved login first. |
| `python campus.py autostart off` | Turns that back off. |
| `python campus.py chatid <token>` | Diagnostic — confirms your Telegram bot can find your chat. |
| `python campus.py bomb` | Deletes everything campus has stored (see below). |

**Autostart** works on Linux (tested), macOS and Windows (implemented, not tested on real
hardware — if it doesn't work, nothing breaks, just run `python campus.py` by hand).

**Telegram setup:** message **@BotFather** on Telegram, `/newbot`, follow the steps for a token.
Message your new bot once. Paste the token when `campus.py` asks. Done — it finds your chat
automatically.

**Calendar setup:** run `python campus.py once`, then in Google Calendar → **Settings → Import &
export → Import**, pick the `campus.ics` it printed. When something changes, the alert reminds
you to re-import — same file, same event IDs, so it updates in place instead of duplicating.

## Deleting everything

```
python campus.py bomb
```

Type `DELETE` to confirm. This stops any running background copy, turns off autostart, and
removes the entire `~/.campus` folder — your saved login, Telegram token, calendar file, and
reminders. Delete `campus.py` itself too and there's no trace left.

## Your data stays on your device

Everything lives in one folder, `~/.campus` (`C:\Users\you\.campus` on Windows):

- your UMS login, saved only if you say yes, in a file only your account can read (not
  encrypted — it's local, and it was your choice to save it)
- the Telegram bot token, your calendar file, your reminders, a private browser profile

Nothing is sent anywhere except: your UMS login to `ums.lpu.in` (that's the login itself), and
change/reminder text to your own Telegram bot if you set one up. **Attendance percentages and
your password never leave your device.**

Not affiliated with or endorsed by Lovely Professional University. MIT licensed.
