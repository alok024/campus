# campus

## Install

```
git clone https://github.com/alok024/campus.git
cd campus
pip install --break-system-packages -r requirements.txt
```

Needs Python 3.9+ and Google Chrome or Microsoft Edge.
Headless Linux server: also `sudo apt install xvfb`.
`python`/`pip` not found: use `python3`/`pip3`.

## Env vars (optional, for non-interactive/headless runs)

| Var | What it does |
|---|---|
| `CAMPUS_USER` / `CAMPUS_PASSWORD` | UMS login, skips the interactive prompt |
| `CAMPUS_TELEGRAM_TOKEN` | Telegram bot token, skips the interactive prompt |
| `CAMPUS_HOME` | Override the `~/.campus` data directory |
| `CAMPUS_QUIET` | Suppress non-error console output |
| `CAMPUS_GOOGLE_CLIENT_ID` / `CAMPUS_GOOGLE_CLIENT_SECRET` | Use your own Google Cloud OAuth client instead of the shared default (all installs share one quota bucket by default; create your own free client in Google Cloud Console → APIs & Services → Credentials if you hit quota errors) |

## Commands

| Command | What it does |
|---|---|
| `python campus.py` | Start the watch loop (every 45 min). First run asks for UMS login, save it?, autostart? |
| `python campus.py once` | Check now, then exit. |
| `python campus.py remind "text" "2026-08-25 17:00"` | Add a reminder. |
| `python campus.py reminders` | List reminders. |
| `python campus.py autostart` | Start on every login. |
| `python campus.py autostart off` | Turn that off. |
| `python campus.py chatid <token>` | Confirm Telegram bot can find your chat. |
| `python campus.py enable calendar` | Connect Google Calendar. |
| `python campus.py enable gmail` | Connect Gmail (read-only). |
| `python campus.py disable calendar` / `disable gmail` | Disconnect one. |
| `python campus.py permissions` | Show what's connected. |
| `python campus.py bomb` | Delete everything. Type `DELETE` to confirm. |

## Telegram setup

1. Message **@BotFather** on Telegram → `/newbot` → get a token.
2. Message your new bot once.
3. `python campus.py chatid <token>`
4. Run `python campus.py` or `once` — it asks to save the token.

## Calendar setup

Manual: `python campus.py once` → Google Calendar → Settings → Import & export → Import → pick `campus.ics`.

Connected: `python campus.py enable calendar` → open the printed link → sign in → Continue → Allow.

## Gmail setup

`python campus.py enable gmail` → open the printed link → sign in → Continue → Allow.

## Phone commands (after Telegram is connected)

| Text this | It does |
|---|---|
| `/status` | What's connected, last sync summary |
| `/sync` | Check UMS now |
| `/remind text \| 2026-08-25 17:00` | Add a reminder |
| `/reminders` | List them |
| `/disable calendar` or `/disable gmail` | Disconnect one |
| `/autostart` or `/autostart off` | Toggle start-on-login |
| `/bomb` | Delete everything (asks to confirm) |
| `/help` | List commands |

## Delete everything

```
python campus.py bomb
```
Type `DELETE` to confirm. Or text `/bomb`, then reply `CONFIRM`.

## Data location

`~/.campus` (`C:\Users\you\.campus` on Windows).

MIT licensed.
