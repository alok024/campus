import getpass
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

from campus.paths import CREDS
from campus.storage import load


def _autostart_desktop_path():
    return Path.home() / ".config" / "autostart" / "campus-agent.desktop"


def _autostart_plist_path():
    return Path.home() / "Library" / "LaunchAgents" / "dev.campus.agent.plist"


def _autostart_task_name():
    return f"campus-{getpass.getuser()}"


def _autostart_enabled():
    system = platform.system()
    if system == "Linux":
        return _autostart_desktop_path().exists()
    if system == "Darwin":
        return _autostart_plist_path().exists()
    if system == "Windows":
        try:
            result = subprocess.run(["schtasks", "/query", "/tn", _autostart_task_name()],
                                     capture_output=True, timeout=10)
            return result.returncode == 0
        except Exception:
            return False
    return False


def _desktop_quote(value):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{escaped}"'


def autostart_enable():
    if not load(CREDS, None):
        print("save your login first (run campus.py and say yes to saving) before enabling autostart.")
        return False
    python = sys.executable
    script = str(Path(__file__).resolve().parent.parent / "campus.py")
    system = platform.system()
    if system == "Linux":
        path = _autostart_desktop_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        exec_line = " ".join(_desktop_quote(p) for p in (python, script))
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=campus\n"
            f"Exec={exec_line}\n"
            "X-GNOME-Autostart-enabled=true\nTerminal=false\n",
            encoding="utf-8",
        )
    elif system == "Darwin":
        path = _autostart_plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            plistlib.dump(
                {"Label": "dev.campus.agent", "ProgramArguments": [python, script], "RunAtLoad": True},
                fh,
            )
        subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True)
        try:
            result = subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True)
        except FileNotFoundError:
            print("couldn't find launchctl — start campus.py by hand instead.")
            return False
        if result.returncode != 0:
            print("macOS refused the autostart entry — start campus.py by hand instead,"
                  " or check System Settings > Login Items.")
            return False
    elif system == "Windows":
        pythonw = str(Path(python).with_name("pythonw.exe"))
        launcher = pythonw if Path(pythonw).exists() else python
        tr = f'"{launcher}" "{script}"'
        try:
            result = subprocess.run(
                ["schtasks", "/create", "/tn", _autostart_task_name(), "/tr", tr, "/sc", "onlogon", "/f"],
                capture_output=True,
            )
        except FileNotFoundError:
            print("couldn't find schtasks — start campus.py by hand instead.")
            return False
        if result.returncode != 0:
            print("windows refused the autostart entry — start campus.py by hand instead.")
            return False
    else:
        print(f"don't know how to autostart on {system} — start campus.py by hand.")
        return False
    print(f"campus will now start automatically when you log in. Undo with: "
          f"{Path(sys.argv[0]).name} autostart off")
    return True


def autostart_disable():
    system = platform.system()
    removed = False
    if system == "Linux":
        path = _autostart_desktop_path()
        removed = path.exists()
        path.unlink(missing_ok=True)
    elif system == "Darwin":
        path = _autostart_plist_path()
        removed = path.exists()
        if removed:
            subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True)
            path.unlink(missing_ok=True)
    elif system == "Windows":
        try:
            result = subprocess.run(
                ["schtasks", "/delete", "/tn", _autostart_task_name(), "/f"], capture_output=True
            )
            removed = result.returncode == 0
        except FileNotFoundError:
            removed = False
    print("autostart removed." if removed
          else "autostart wasn't on, or couldn't be removed automatically"
               " — check your OS's startup/login-items settings.")
    return removed
