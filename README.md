# campus

LPU's UMS and the LPU Touch app never tell you when a class is cancelled or moved — you
find out by walking to an empty room. **campus** is a tiny agent that watches UMS for you.

It logs into UMS every 45 minutes, notices what changed, and tells you — a desktop pop-up,
and on your phone if you connect a Telegram bot. It also keeps a calendar file up to date and
reminds you about things you ask it to remember.

It's one Python file. It runs on your own laptop. There is no server, no account, no website
storing anything.

---

## What it does

- **Tells you when a class is cancelled, moved, or a new UMS notice is posted** — e.g.
  `CANCELLED: Wed 10:20 CAP7001`, `MOVED: Mon 12:00 CAB106: 36-603 -> 38-610`. Class-related
  notices are flagged.
- **Keeps your timetable on your phone** — writes a `campus.ics` calendar file (classes, exams,
  holidays). Import it into Google Calendar once; because events keep the same IDs, re-importing
  after a change updates them in place instead of making duplicates.
- **Remembers your own tasks** — tell it what to do and when, and it reminds you (and puts it on
  the calendar).

## Install

You need **Python 3.9+**, **Google Chrome or Microsoft Edge** (already on most machines), and one
small package:

```
pip install websocket-client
```

Then download **[`campus.py`](https://raw.githubusercontent.com/alok024/campus/main/campus.py)**
into a folder (right-click → Save As, or `curl -O <that link>`).

> **Getting `error: externally-managed-environment`?** Newer Linux (Ubuntu/Mint/Debian) and Mac
> (Homebrew) Python block plain `pip install` to protect the system. Run:
> ```
> pip install --break-system-packages websocket-client
> ```
> `websocket-client` is one small pure-Python package, so this is low-risk. Prefer not to touch
> system packages at all? Use a virtual environment instead:
> ```
> python3 -m venv ~/.campus-env
> ~/.campus-env/bin/pip install websocket-client
> ```
> and run the tool with `~/.campus-env/bin/python campus.py` from then on.

> On a Linux server with no screen, also `sudo apt install xvfb` — UMS's bot-check needs a real
> browser, and Xvfb gives it an invisible one. On a normal laptop you don't need this.

## Run it

```
python campus.py
```

The first time, it asks for your UMS registration number and password (used only on your
machine, sent only to `ums.lpu.in`), and offers to save them so it won't ask again. If you save
them, it also offers to **start automatically every time you log in** — say yes and you never
have to open a terminal for this again, even after a reboot. It also offers to connect a Telegram
bot for phone alerts. Then it watches UMS every 45 minutes and tells you when something changes.
If you didn't turn on autostart, leave the window running (minimise it).

**Other commands:**

```
python campus.py once                                  # check right now, then exit
python campus.py remind "submit DBMS assignment" "2026-08-25 17:00"
python campus.py reminders                             # list what you asked it to remember
python campus.py autostart                              # turn on start-at-login
python campus.py autostart off                          # turn it back off
python campus.py bomb                                  # delete everything (see below)
```

## Getting the alerts on your phone (recommended)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the steps — it gives you a token.
2. Open a chat with **your new bot** and send it any message (like "hi").
3. When `campus.py` asks about Telegram, paste the token. It finds your chat automatically.

Now cancellations and reminders reach your phone even when the laptop screen is off.

## Starting automatically after a reboot

Say yes when it offers this on first run, or turn it on any time:

```
python campus.py autostart
```

This makes campus start itself the next time you log in — no need to open a terminal and run it
by hand again. It only works if you've saved your login (autostart has nothing to run otherwise).
To turn it back off: `python campus.py autostart off`. It's also removed automatically if you
run `python campus.py bomb`.

- **Linux** — adds a start-on-login entry (`~/.config/autostart`). Live-tested on Linux Mint.
- **macOS** — adds a Login Item (`~/Library/LaunchAgents`). Implemented and code-reviewed, but not
  tested on an actual Mac — if it doesn't work, `python campus.py once` still works fine, just run
  it manually.
- **Windows** — adds a Task Scheduler entry that runs at logon. Same caveat: not tested on a real
  Windows machine yet.

If autostart doesn't work on your machine, nothing is broken — just run `python campus.py` by
hand, or see the always-on options below.

When running via autostart there's no visible window, so its output goes to
`~/.campus/campus.log` instead — check that file if you want to see what it's been doing. If UMS
login starts failing repeatedly (most likely because your password changed — UMS forces a reset
every 90 days), campus notices after 3 failed attempts in a row and sends you a notification
saying so, instead of silently going quiet forever.

If you move or rename `campus.py` (or the folder it's in) after turning autostart on, the entry
still points at the old location and will quietly fail. Turn autostart off first, move things,
then turn it back on.

## Keeping it always on

campus only catches a change while it's running, so for round-the-clock alerts it needs to live
on a machine that stays on. The simplest options:

- **Leave it running on your laptop** (with autostart on, above). Fine if your laptop is usually
  on and online. Sleep pauses it; it resumes when the laptop wakes.
- **A spare/old machine at home** — an old laptop, a mini-PC, a Raspberry Pi with Chromium — left
  plugged in, with autostart on. Telegram still delivers to your phone wherever you are. This is
  the real "set and forget" setup.

To run it without it asking questions each time (useful for a background machine), set the login
as environment variables and it won't prompt:

```
CAMPUS_USER=12345678 CAMPUS_PASSWORD=yourpass python campus.py
```

**Free cloud hosting will not work**, and I tested this so you don't have to: UMS sits behind
Cloudflare, which blocks the datacenter IP addresses that GitHub Actions, Oracle/AWS/GCP free
tiers, and the like run on — the login never gets through. A real browser on a normal home
internet connection is what passes the check. So "always on" means a device at home, not a
free server.

## Your calendar, on your phone

Run once so `campus.ics` exists (it prints the full path). In Google Calendar → **Settings →
Import & export → Import**, pick that file. Your classes show up with rooms; exams and holidays
too. When the timetable changes, campus rewrites the file — re-import it and the events update in
place.

## Where your stuff lives, and deleting it

Everything sits in a single folder — `~/.campus` (Windows: `C:\Users\you\.campus`):

- your saved login (a file only your user account can read — **not** encrypted; it's on your own
  machine, and you chose to save it),
- the Telegram token, the calendar file, your reminders, and a private browser profile.

Your **attendance percentages and password never leave your device.** Phone alerts carry course
codes, rooms and times only.

To wipe all of it — login, token, calendar, reminders, everything:

```
python campus.py bomb
```

It asks you to type `DELETE`, then turns off autostart (if it was on) and removes the whole
`~/.campus` folder. Delete `campus.py` too and there's no trace left.

## Honest limitations

- **It checks every 45 minutes, not instantly.** UMS has no live feed, so campus polls. A class
  cancelled at 9:50 for a 10:20 class is usually caught in time, but not guaranteed to the minute.
- **A Chrome/Edge window opens briefly during each check** — that's UMS's bot-check demanding a
  real browser, not a virus. It closes itself.
- **Google Calendar updates need a re-import**, not magic — see above. (Fully-automatic syncing
  would need a Google login the tool deliberately doesn't ask for.)
- **Personal use only.** It reads UMS with credentials you provide, on your own device. Use it in
  line with your university's IT policy. You're responsible for your own account.

Not affiliated with or endorsed by Lovely Professional University. MIT licensed.
