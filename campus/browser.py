import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from campus.paths import HOME, PROFILE, UmsError, WINDOW


def chrome_binary():
    system = platform.system()
    if system == "Windows":
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
            base = os.environ.get(var)
            if base:
                for rel in (r"Google\Chrome\Application\chrome.exe",
                            r"Microsoft\Edge\Application\msedge.exe"):
                    p = Path(base) / rel
                    if p.exists():
                        return str(p)
        for name in ("chrome.exe", "msedge.exe"):
            found = shutil.which(name)
            if found:
                return found
        raise UmsError("Chrome or Edge not found; install Google Chrome")
    if system == "Darwin":
        for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
            if Path(p).exists():
                return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "microsoft-edge", "microsoft-edge-stable"):
        found = shutil.which(name)
        if found:
            return found
    raise UmsError("no chrome/chromium/edge found; install Google Chrome")


_active_browser = None


def active_browser():
    return _active_browser


class Browser:
    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc = None
        self.xvfb = None
        self.xauth = None
        self.target = None

    def __enter__(self):
        global _active_browser
        try:
            self._start()
            _active_browser = self
            return self
        except Exception:
            self.close()
            raise

    def _start(self):
        env = dict(os.environ)
        system = platform.system()
        if system not in ("Windows", "Darwin") and not env.get("DISPLAY"):
            if not shutil.which("Xvfb"):
                raise UmsError("no screen and no Xvfb; run from your desktop or: sudo apt install xvfb")
            have_xauth = shutil.which("xauth")
            if have_xauth:
                HOME.mkdir(parents=True, exist_ok=True)
                self.xauth = HOME / f"xvfb-{os.getpid()}.xauth"
                cookie = secrets.token_hex(16)
            for n in range(80, 100):
                if Path(f"/tmp/.X{n}-lock").exists():
                    continue
                xvfb_argv = ["Xvfb", f":{n}", "-screen", "0", f"{WINDOW[0]}x{WINDOW[1]}x24", "-nolisten", "tcp"]
                if have_xauth:
                    subprocess.run(["xauth", "-f", str(self.xauth), "add", f":{n}", ".", cookie],
                                    capture_output=True)
                    env["XAUTHORITY"] = str(self.xauth)
                    xvfb_argv += ["-auth", str(self.xauth)]
                cand = subprocess.Popen(xvfb_argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.5)
                if cand.poll() is None:
                    self.xvfb, env["DISPLAY"] = cand, f":{n}"
                    break
            else:
                raise UmsError("no free X display")
        PROFILE.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                PROFILE.chmod(0o700)
            except OSError:
                pass
        argv = [chrome_binary(), f"--remote-debugging-port={self.port}",
                "--remote-debugging-address=127.0.0.1", f"--user-data-dir={PROFILE}",
                f"--window-size={WINDOW[0]},{WINDOW[1]}",
                f"--remote-allow-origins=http://127.0.0.1:{self.port}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-accelerated-video-decode", "--disable-gpu", "about:blank"]
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                self._targets()
                self.target = self._open_tab()
                return self
            except UmsError:
                raise
            except Exception:
                time.sleep(0.5)
        raise UmsError("chrome did not open a debug port in 30s")

    def _open_tab(self):
        url = f"http://127.0.0.1:{self.port}/json/new?url=about:blank"
        try:
            req = urllib.request.Request(url, method="PUT")
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)["id"]
        except urllib.error.HTTPError:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.load(r)["id"]

    def __exit__(self, *exc):
        self.close()

    def close(self):
        global _active_browser
        for child in (self.proc, self.xvfb):
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
        self.proc = self.xvfb = None
        if self.xauth:
            Path(self.xauth).unlink(missing_ok=True)
            self.xauth = None
        if _active_browser is self:
            _active_browser = None

    def _targets(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=5) as r:
            data = json.load(r)
        pages = [t for t in data if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        if not pages:
            raise UmsError("chrome has no page target")
        return pages

    def _socket(self):
        import websocket
        pages = self._targets()
        chosen = next((t for t in pages if t["id"] == self.target), None) or pages[0]
        self.target = chosen["id"]
        return websocket.create_connection(chosen["webSocketDebuggerUrl"], max_size=None,
                                            timeout=60, suppress_origin=True)

    def _call(self, method, params=None):
        ws = self._socket()
        try:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    return msg
        finally:
            ws.close()

    def js(self, expr):
        msg = self._call("Runtime.evaluate",
                         {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if msg.get("error"):
            raise UmsError("devtools refused the call")
        result = msg.get("result", {})
        if result.get("exceptionDetails"):
            raise UmsError("page script raised while reading the portal")
        return result.get("result", {}).get("value")

    def click(self, x, y):
        for kind in ("mousePressed", "mouseReleased"):
            self._call("Input.dispatchMouseEvent",
                       {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})

    def _box(self, selector, min_size=0):
        return self.js(
            "(function(){var e=document.querySelector(" + json.dumps(selector) + ");if(!e)return null;"
            "e.scrollIntoView({block:'center'});var r=e.getBoundingClientRect();"
            + (f"if(r.width<{min_size}||r.height<{min_size})return null;" if min_size else "") +
            "return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()")

    def type_into(self, selector, text):
        box = self._box(selector)
        if not box:
            raise UmsError(f"no element matched {selector}")
        pt = json.loads(box)
        self.click(pt["x"], pt["y"])
        self._call("Input.insertText", {"text": text})

    def click_selector(self, selector):
        box = self._box(selector, min_size=2)
        if not box:
            return False
        pt = json.loads(box)
        self.click(pt["x"], pt["y"])
        return True

    def goto(self, url, settle=2.0, budget=40.0):
        self._call("Page.navigate", {"url": url})
        last, stable, waited = -1, 0, 0.0
        while waited < budget:
            time.sleep(settle)
            waited += settle
            size = self.js("document.body?document.body.innerText.length:0")
            state = self.js("document.readyState")
            if state == "complete" and size == last and isinstance(size, int) and size > 40:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
            last = size
        return False

    def text(self):
        return self.js("document.body?document.body.innerText:''") or ""

    def url(self):
        return self.js("location.href") or ""


def close_active():
    if _active_browser is not None:
        try:
            _active_browser.close()
        except Exception:
            pass
