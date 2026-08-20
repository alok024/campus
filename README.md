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

Then download **[`campus.py`](campus.py)** into a folder.

> On a Linux server with no screen, also `sudo apt install xvfb` — UMS's bot-check needs a real
> browser, and Xvfb gives it an invisible one. On a normal laptop you don't need this.

## Run it

```
python campus.py
```

The first time, it asks for your UMS registration number and password (used only on your
machine, sent only to `ums.lpu.in`), and offers to save them so it won't ask again. It also
offers to connect a Telegram bot for phone alerts. Then it watches UMS every 45 minutes and
tells you when something changes. Leave it running (minimise the window).

**Other commands:**

```
python campus.py once                                  # check right now, then exit
python campus.py remind "submit DBMS assignment" "2026-08-25 17:00"
python campus.py reminders                             # list what you asked it to remember
python campus.py bomb                                  # delete everything (see below)
```

## Getting the alerts on your phone (recommended)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the steps — it gives you a token.
2. Open a chat with **your new bot** and send it any message (like "hi").
3. When `campus.py` asks about Telegram, paste the token. It finds your chat automatically.

Now cancellations and reminders reach your phone even when the laptop screen is off.

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

It asks you to type `DELETE`, then removes the whole `~/.campus` folder. Delete `campus.py` too
and there's no trace left.

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
