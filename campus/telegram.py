import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from campus.paths import CONFIG
from campus.storage import ask, load, save


def telegram_creds():
    cfg = load(CONFIG, {})
    token = cfg.get("telegram_token") or os.environ.get("CAMPUS_TELEGRAM_TOKEN")
    chat = cfg.get("telegram_chat") or os.environ.get("CAMPUS_TELEGRAM_CHAT")
    return token, chat


def notify(title, message):
    _desktop(title, message)
    token, chat = telegram_creds()
    if token and chat:
        _telegram(token, chat, title, message)


def _applescript_escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _desktop(title, message):
    system = platform.system()
    try:
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", "--app-name=campus", "--", title, message],
                           timeout=10, check=False)
            return
        if system == "Darwin":
            script = (f'display notification "{_applescript_escape(message)}" '
                      f'with title "{_applescript_escape(title)}"')
            subprocess.run(["osascript", "-e", script], timeout=10, check=False)
            return
        if system == "Windows":
            ps = ("$ErrorActionPreference='SilentlyContinue';"
                  "Add-Type -AssemblyName System.Windows.Forms;"
                  "$n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                  "$n.ShowBalloonTip(10000,$env:CAMPUS_NOTIFY_TITLE,$env:CAMPUS_NOTIFY_MSG,"
                  "[System.Windows.Forms.ToolTipIcon]::Info);Start-Sleep -Seconds 6;$n.Dispose()")
            env = dict(os.environ, CAMPUS_NOTIFY_TITLE=title, CAMPUS_NOTIFY_MSG=message)
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           timeout=15, check=False, env=env)
            return
        print(f"campus: no desktop notifier on this machine — {title}: {message}")
    except Exception as exc:
        print(f"campus: desktop notification failed ({exc!r}) — {title}: {message}")


def _telegram(token, chat, title, message):
    data = urllib.parse.urlencode({"chat_id": chat, "text": f"{title}\n{message}"[:4000],
                                   "disable_web_page_preview": "true"}).encode()
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                         data=data, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                if 200 <= r.status < 300:
                    return
                print(f"campus: telegram notify got status {r.status}")
        except Exception as exc:
            if attempt == 2:
                print(f"campus: telegram notify failed: {exc!r}")
                return
            time.sleep(2)


def telegram_chat_id(token):
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token.strip()}/getUpdates?timeout=0",
                                    timeout=15) as r:
            body = json.load(r)
    except Exception:
        return None
    for update in reversed(body.get("result", [])):
        chat = (update.get("message") or update.get("edited_message") or {}).get("chat", {})
        if chat.get("id") is not None and chat.get("type") == "private":
            return str(chat["id"])
    return None


def _telegram_get(token, params):
    url = f"https://api.telegram.org/bot{token}/getUpdates?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=params.get("timeout", 0) + 10) as r:
            return json.load(r)
    except Exception:
        return None


def setup_telegram():
    if os.environ.get("CAMPUS_TELEGRAM_TOKEN"):
        return
    if not sys.stdin.isatty():
        return
    cfg = load(CONFIG, {})
    if cfg.get("telegram_token"):
        return
    print("\nWant alerts on your phone via a Telegram bot? (desktop pop-ups also work, if this"
          " machine has a notifier installed — e.g. notify-send on Linux.)")
    if ask("  set up Telegram now? [y/N]: ").strip().lower() not in ("y", "yes"):
        return
    print("  1) in Telegram, message @BotFather -> /newbot -> follow it -> copy the token")
    print("  2) open a chat with YOUR new bot and send it any message (e.g. hi)")
    token = ask("  bot token: ").strip()
    if not token:
        return
    chat = telegram_chat_id(token)
    if not chat:
        print("  couldn't find your message to the bot — send it 'hi' first, then rerun. Skipping.")
        return
    cfg.update(telegram_token=token, telegram_chat=chat)
    save(CONFIG, cfg)
    _telegram(token, chat, "campus", "connected — you'll get alerts here")
    print("  connected. Check Telegram for a test message.")
