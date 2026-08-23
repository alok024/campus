import getpass
import json
import os
import sys

from campus.paths import CREDS, HOME


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def ask_secret(prompt):
    try:
        return getpass.getpass(prompt)
    except EOFError:
        return ""


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path, data):
    HOME.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def redirect_to_log_if_headless(log_path):
    try:
        if sys.stdout.isatty():
            return
    except (AttributeError, ValueError):
        pass
    HOME.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log


def credentials(autostart_enable_fn):
    from campus.paths import UmsError

    env_user, env_pass = os.environ.get("CAMPUS_USER"), os.environ.get("CAMPUS_PASSWORD")
    if env_user and env_pass:
        return env_user.strip(), env_pass.strip()
    saved = load(CREDS, None)
    if saved and saved.get("user") and saved.get("password"):
        return saved["user"], saved["password"]
    if not sys.stdin.isatty():
        raise UmsError("no saved login and no terminal to ask for one — set CAMPUS_USER/"
                       "CAMPUS_PASSWORD or run 'python campus.py' interactively once first")
    print("\nLog into UMS (used only on this machine, sent only to ums.lpu.in):")
    user = ask("  registration number: ").strip()
    password = ask_secret("  UMS password (hidden): ").strip()
    if not user or not password:
        raise UmsError("both a registration number and password are required")
    keep = ask("  save these so it doesn't ask again? [y/N]: ").strip().lower() in ("y", "yes")
    if keep:
        save(CREDS, {"user": user, "password": password})
        print(f"  saved to {CREDS} (owner-only). Use 'python campus.py bomb' to wipe everything.")
        if ask("  start automatically when you log in, so you don't have to run this by hand"
               " after a reboot? [y/N]: ").strip().lower() in ("y", "yes"):
            autostart_enable_fn()
    else:
        print("  not saved — it'll ask again next run.")
    return user, password
