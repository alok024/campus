import os
import time
from datetime import datetime, timedelta

from campus.autostart import _autostart_enabled, autostart_disable, autostart_enable
from campus.google import _google_token_path, google_disable
from campus.paths import BOTFILE, GOOGLE_SCOPES, LOCK, STATE, TASKS, UmsError
from campus.reminders import add_reminder
from campus.storage import load, save
from campus.sync import _bomb_now, check
from campus.telegram import _telegram, _telegram_get, telegram_creds


def _bot_status_text():
    state = load(STATE, None)
    lines = [f"sessions: {len(state.get('sessions', []))}, notices: {len(state.get('messages', []))}"
             if state else "no UMS sync yet"]
    for module in GOOGLE_SCOPES:
        connected = bool(load(_google_token_path(module), None))
        lines.append(f"{module}: {'connected' if connected else 'off'}")
    lines.append(f"autostart: {'on' if _autostart_enabled() else 'off'}")
    return "\n".join(lines)


def _bot_help_text():
    return ("/status — what's connected and last sync\n"
            "/sync — check UMS right now\n"
            '/remind text | 2026-08-25 17:00 — add a reminder\n'
            "/reminders — list them\n"
            "/disable calendar or /disable gmail — disconnect one\n"
            "/autostart or /autostart off\n"
            "/bomb — delete everything (asks to confirm)")


def _bot_command(token, chat, text, user, password, bomb_deadline, drain_fn=None):
    if bomb_deadline[0] and time.time() < bomb_deadline[0] and text.strip().upper() == "CONFIRM":
        bomb_deadline[0] = None
        _telegram(token, chat, "campus", "wiping everything — campus is shutting down now.")
        if drain_fn:
            try:
                drain_fn()
            except Exception:
                pass
        try:
            _bomb_now()
        finally:
            os._exit(0)
    bomb_deadline[0] = None
    parts = text.split(None, 1)
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    if cmd == "status":
        _telegram(token, chat, "campus", _bot_status_text())
    elif cmd in ("sync", "check"):
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 300:
            _telegram(token, chat, "campus", "sync already running, try again in a minute.")
        else:
            try:
                diff = check(user, password)
                body = "\n".join(f"- {c}" for c in diff) if diff else "no change"
            except UmsError as exc:
                body = f"sync failed: {exc}"
            _telegram(token, chat, "campus", body)
    elif cmd == "remind":
        if "|" in rest:
            text_part, when_part = (p.strip() for p in rest.split("|", 1))
            try:
                when = datetime.fromisoformat(when_part.replace(" ", "T", 1))
                when_iso = when.isoformat(timespec="minutes")
                add_reminder(text_part, when_iso)
                _telegram(token, chat, "campus", f"reminder set: {text_part} — {when_iso}")
            except ValueError:
                _telegram(token, chat, "campus", 'use: /remind text | 2026-08-25 17:00')
        else:
            _telegram(token, chat, "campus", 'use: /remind text | 2026-08-25 17:00')
    elif cmd == "reminders":
        tasks = load(TASKS, [])
        body = "\n".join(f"{t['when']}  {t['text']}" for t in tasks) if tasks else "no reminders"
        _telegram(token, chat, "campus", body)
    elif (cmd in ("enable", "disable") and rest.split(None, 1)[:1]
          and rest.split(None, 1)[0].lower() in GOOGLE_SCOPES):
        module = rest.split(None, 1)[0].lower()
        if cmd == "disable":
            google_disable(module)
            _telegram(token, chat, "campus", f"{module} disconnected.")
        else:
            _telegram(token, chat, "campus",
                      f"connecting {module} needs a one-time browser step on the computer campus "
                      f"runs on — run 'python campus.py enable {module}' there.")
    elif cmd == "autostart":
        if rest.strip().lower() == "off":
            autostart_disable()
            _telegram(token, chat, "campus", "autostart turned off.")
        else:
            ok = autostart_enable()
            _telegram(token, chat, "campus", "autostart turned on." if ok else "couldn't turn on autostart.")
    elif cmd == "bomb":
        bomb_deadline[0] = time.time() + 60
        _telegram(token, chat, "campus", "reply CONFIRM within 60 seconds to delete everything.")
    elif cmd == "help":
        _telegram(token, chat, "campus", _bot_help_text())
    else:
        _telegram(token, chat, "campus", "unknown command. /help for the list.")


def telegram_listen(stop_event, user, password):
    token, chat = telegram_creds()
    if not token or not chat:
        return
    state = load(BOTFILE, {"last_update_id": 0})
    bomb_deadline = [None]
    started_at = time.time()
    while not stop_event.is_set():
        try:
            body = _telegram_get(token, {"timeout": 25, "offset": state["last_update_id"] + 1})
            if not body or not body.get("ok"):
                if stop_event.wait(5):
                    return
                continue
            for update in body.get("result", []):
                uid = update.get("update_id")
                if uid is None:
                    continue
                state["last_update_id"] = max(state.get("last_update_id", 0), uid)
                msg = update.get("message") or {}
                if msg.get("date", 0) < started_at:
                    save(BOTFILE, state)
                    continue
                if msg.get("chat", {}).get("type") != "private":
                    continue
                incoming_from = str((msg.get("from") or {}).get("id", ""))
                text = (msg.get("text") or "").strip()
                if incoming_from != str(chat) or not text:
                    continue
                save(BOTFILE, state)

                def _drain(tok=token, last=state):
                    _telegram_get(tok, {"timeout": 0, "offset": last["last_update_id"] + 1})

                try:
                    _bot_command(token, chat, text, user, password, bomb_deadline, drain_fn=_drain)
                except Exception as exc:
                    _telegram(token, chat, "campus", f"command failed: {exc}")
            save(BOTFILE, state)
        except Exception:
            if stop_event.wait(10):
                return
