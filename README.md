# campus

LPU's UMS and LPU Touch never tell you when a class is cancelled or moved. **campus** watches
UMS for you and tells you — desktop pop-up, Telegram, and your calendar — the moment something
changes. One Python file. Runs on your own machine. No server, no account, no website.

## What it provides

- **Change alerts** — cancelled/moved classes and new UMS notices, e.g. `CANCELLED: Wed 10:20
  CAP7001`, `MOVED: Mon 12:00 CAB106: 36-603 -> 38-610`. Checked every 45 minutes (UMS has no
  live feed, so this isn't instant, but it's fast enough to catch a class before it starts).
- **Calendar** — writes `campus.ics` (classes, exams, holidays) kept current automatically;
  import it into Google Calendar and re-imports update events in place, no duplicates. Or connect
  it directly (`enable calendar`) and skip the manual re-import entirely.
- **Gmail alerts (optional)** — connect it (`enable gmail`) and campus watches for fee/exam/
  placement-related mail and notifies you, the same way it does for UMS changes. Read-only —
  it can never delete, label, or send anything.
- **Reminders** — tell it what to do and when; it notifies you and puts it on the calendar.
- **Phone alerts and control** — connect a Telegram bot and every alert reaches your phone, screen
  off or not. Once it's connected, you can also run campus from your phone — check status, force
  a sync, add reminders, turn things off, even wipe everything — by texting the bot. No terminal
  needed after the first setup.
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
| `python campus.py enable calendar` | Connects your Google Calendar — opens a browser, you approve, done. |
| `python campus.py enable gmail` | Connects Gmail (read-only) for important-mail alerts. |
| `python campus.py disable calendar` / `disable gmail` | Disconnects one, revokes its access. |
| `python campus.py permissions` | Shows what's currently connected. |
| `python campus.py bomb` | Deletes everything campus has stored (see below). |

**Autostart** works on Linux (tested), macOS and Windows (implemented, not tested on real
hardware — if it doesn't work, nothing breaks, just run `python campus.py` by hand).

**Telegram setup:** message **@BotFather** on Telegram, `/newbot`, follow the steps for a token.
Message your new bot once. Paste the token when `campus.py` asks. Done — it finds your chat
automatically.

**Calendar setup (manual, always works):** run `python campus.py once`, then in Google Calendar →
**Settings → Import & export → Import**, pick the `campus.ics` it printed. When something changes,
the alert reminds you to re-import — same file, same event IDs, so it updates in place instead of
duplicating.

**Calendar/Gmail setup (connected, no manual re-import):** run `python campus.py enable calendar`
or `enable gmail`. It prints a link — open it (needs a real browser, on the same machine you're
running campus on), sign in, click Allow. Two things you'll see that are normal, not errors:

- **"Google hasn't verified this app"** — expected. This is a small tool, not something that's
  gone through Google's formal review process. You're both the developer and the only approved
  user, so it's safe to click through. Click "Continue" (not "Back to safety").
- campus creates its **own separate calendar** ("campus (LPU timetable)") rather than writing into
  your main one — so it's easy to tell apart, and `bomb` or `disable calendar` removes it cleanly
  without touching anything else on your Google Calendar.

Calendar connects with events read/write access to just that one calendar it creates — never your
existing calendars. Gmail connects read-only, subject lines only, never your other calendars,
never full message bodies. `python campus.py permissions` shows what's currently connected.

## Controlling it from your phone

Once you've connected Telegram, campus listens for commands from that same chat — so after the
one-time setup (on whatever computer campus is running on), you never need a terminal again:

| Text this | It does |
|---|---|
| `/status` | What's connected, and the last sync summary |
| `/sync` | Check UMS right now |
| `/remind text \| 2026-08-25 17:00` | Add a reminder |
| `/reminders` | List them |
| `/disable calendar` or `/disable gmail` | Disconnect one |
| `/autostart` or `/autostart off` | Toggle start-on-login |
| `/bomb` | Delete everything (replies asking you to confirm) |
| `/help` | List of commands |

Only the chat that's already connected can control it — messages from anyone else are ignored.
One thing that can't happen over Telegram: **connecting** Calendar or Gmail for the first time —
that needs an actual browser on the machine campus runs on (that's how Google's approval screen
works, not a campus limitation). Texting `/enable calendar` explains this and tells you the
command to run there instead. Once connected, everything else — including disconnecting — works
from your phone.

## Deleting everything

```
python campus.py bomb
```

Type `DELETE` to confirm. This stops any running background copy, turns off autostart, disconnects
Calendar and Gmail (revoking their access and deleting the campus calendar it created), and
removes the entire `~/.campus` folder — your saved login, Telegram token, calendar file, and
reminders. Delete `campus.py` itself too and there's no trace left.

Or text `/bomb` from your phone — it replies asking you to reply `CONFIRM` within 60 seconds, then
does the same thing.

## Where your data goes

Everything lives in one folder, `~/.campus` (`C:\Users\you\.campus` on Windows): your UMS login
(saved only if you say yes, in a file only your account can read, not encrypted — it's local, and
it was your choice), the Telegram bot token, your Calendar/Gmail connection (a revocable token,
not your Google password), your calendar file, your reminders, a private browser profile.

**If you're not using Calendar/Gmail:** nothing leaves your device except your UMS login to
`ums.lpu.in`, and change/reminder text to your own Telegram bot if you set one up. Attendance
percentages and your password never leave your device.

**If you connect Calendar or Gmail:** the relevant data genuinely goes through Google's own
servers — that's what "connecting" means. campus only ever sends your timetable (to create
calendar events) or reads your mail's subject lines (to check for important ones) — never your
UMS password, never full email bodies, never anything to any server but Google's and your own
Telegram bot.

Not affiliated with or endorsed by Lovely Professional University. MIT licensed.
