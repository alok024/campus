#!/usr/bin/env python3
"""campus — a one-file agent that watches LPU UMS so you don't have to.

It logs into UMS every 45 minutes, notices when a class is cancelled or moved or a
new notice is posted, tells you, keeps a calendar file up to date, and reminds you
about things you ask it to remember. Everything runs on your own machine.

Run it:            python campus.py
Add a reminder:    python campus.py remind "submit DBMS assignment" 2026-08-25 17:00
List reminders:    python campus.py reminders
Sync once and exit:python campus.py once
Start on login:    python campus.py autostart
Stop that:         python campus.py autostart off
Connect Google:    python campus.py enable calendar   (or: gmail)
Disconnect:        python campus.py disable calendar  (or: gmail)
What's connected:  python campus.py permissions
Delete everything: python campus.py bomb

Needs: Python 3.9+, Google Chrome or Microsoft Edge, and one package:
    pip install websocket-client
"""

from __future__ import annotations

import getpass
import hashlib
import http.client
import http.server
import json
import os
import platform
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path(os.environ.get("CAMPUS_HOME") or (Path.home() / ".campus"))
STATE = HOME / "state.json"
CONFIG = HOME / "config.json"
CREDS = HOME / "creds.json"
TASKS = HOME / "reminders.json"
ICS = HOME / "campus.ics"
PROFILE = HOME / "browser"
LOCK = HOME / "campus.lock"
PIDFILE = HOME / "campus.pid"
FAILS = HOME / "fails.json"
LOG = HOME / "campus.log"

PORTAL = "https://ums.lpu.in/lpuums/"
TIMETABLE = PORTAL + "Reports/frmStudentTimeTable.aspx"
SPA = PORTAL + "openapp.aspx?from=ums&toApp=nextproject&pagename="
INTERVAL_MIN = 45

GOOGLE_CLIENT_ID = "665688308829-cettf0u20bkg2ju268nbhbhfoo4pv7hr.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_TZ = "Asia/Kolkata"
GOOGLE_TZ_OFFSET = timedelta(hours=5, minutes=30)
GOOGLE_SCOPES = {
    "calendar": "https://www.googleapis.com/auth/calendar.app.created",
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
}
IMPORTANT_MAIL_KEYWORDS = (
    "fee", "exam", "datesheet", "date sheet", "placement", "interview",
    "shortlist", "hostel", "hall ticket", "admit card", "backlog", "reappear",
)
IMPORTANT_MAIL_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in IMPORTANT_MAIL_KEYWORDS) + r")\b", re.IGNORECASE)
WINDOW = (1600, 1200)
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SHORT = {d: d[:3] for d in DAYS}
BYDAY = {"Mon": "MO", "Tue": "TU", "Wed": "WE", "Thu": "TH", "Fri": "FR", "Sat": "SA", "Sun": "SU"}
DAYNUM = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


class UmsError(RuntimeError):
    pass


ADMIT = {
    "course": re.compile(r"[A-Z]{2,4}\d{3,4}"),
    "room": re.compile(r"\d{2}-\d{3}[A-Z]?(?: [A-Z])?"),
    "clock": re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d"),
    "kind": re.compile(r"(?:Lecture|Practical|Tutorial)"),
    "group": re.compile(r"(?:All|\d{1,2})"),
    "isodate": re.compile(r"\d{4}-\d{2}-\d{2}"),
}
MARKS = {"rgb(255, 77, 77)": "mid-term-test", "rgb(255, 160, 0)": "term-boundary",
         "rgb(25, 118, 210)": "holiday"}
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
          "Sept": 9, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
CLASS_WORDS = re.compile(
    r"\b(cancel|resch|postpon|prepon|class|lecture|tuition|extra|makeup|venue|room|"
    r"timetable|exam|datesheet|practical|holiday)\w*", re.I)


def admit(kind, value):
    if value is None:
        return None
    text = str(value).strip()
    pattern = ADMIT.get(kind)
    if not pattern or not pattern.fullmatch(text):
        return None
    if kind == "isodate":
        try:
            date.fromisoformat(text)
        except ValueError:
            return None
    return text


def admit_int(value, low, high):
    try:
        n = int(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def fingerprint(text):
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().encode()).hexdigest()[:12]


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


class Browser:
    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc = None
        self.xvfb = None
        self.target = None

    def __enter__(self):
        try:
            return self._start()
        except Exception:
            self.close()
            raise

    def _start(self):
        env = dict(os.environ)
        system = platform.system()
        if system not in ("Windows", "Darwin") and not env.get("DISPLAY"):
            if not shutil.which("Xvfb"):
                raise UmsError("no screen and no Xvfb; run from your desktop or: sudo apt install xvfb")
            for n in range(80, 100):
                if Path(f"/tmp/.X{n}-lock").exists():
                    continue
                cand = subprocess.Popen(
                    ["Xvfb", f":{n}", "-screen", "0", f"{WINDOW[0]}x{WINDOW[1]}x24", "-nolisten", "tcp"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.5)
                if cand.poll() is None:
                    self.xvfb, env["DISPLAY"] = cand, f":{n}"
                    break
            else:
                raise UmsError("no free X display")
        PROFILE.mkdir(parents=True, exist_ok=True)
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
        for child in (self.proc, self.xvfb):
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
        self.proc = self.xvfb = None

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


FORM_STATE_JS = """
(function(){
  var u=document.querySelector('#txtU');
  var p=document.querySelector('input[type=password]');
  var c=document.querySelector('[id^=cf-chl-widget]');
  return JSON.stringify({user: u?u.value:null, pass: p?p.value.length:null,
                         token: c?(c.value||'').length:0, ready: document.readyState});
})()
"""


def login(browser, user, password, attempts=3):
    for _ in range(attempts):
        browser.goto(PORTAL)
        deadline = time.time() + 45
        ready = False
        while time.time() < deadline:
            if "StudentDashboard" in browser.url():
                return
            if browser.js("!!document.querySelector('input[type=password]')"):
                ready = True
                break
            time.sleep(2)
        if not ready:
            continue
        browser.type_into("#txtU", user)
        browser.type_into("input[type=password]", password)
        deadline = time.time() + 25
        state = {}
        while time.time() < deadline:
            time.sleep(2)
            state = json.loads(browser.js(FORM_STATE_JS) or "{}")
            has_widget = browser.js("!!document.querySelector('[id^=cf-chl-widget]')")
            if state.get("ready") == "complete" and (state.get("token") or not has_widget):
                break
        if state.get("user") != user:
            browser.type_into("#txtU", user)
        browser.type_into("input[type=password]", password)
        state = json.loads(browser.js(FORM_STATE_JS) or "{}")
        if state.get("user") != user or state.get("pass") != len(password):
            continue
        if not browser.click_selector("input[type=submit]"):
            continue
        for _ in range(8):
            time.sleep(3)
            if "StudentDashboard" in browser.url():
                return
    raise UmsError("login did not reach the dashboard; check your reg number/password "
                   "(UMS forces a password change every 90 days)")


GRID_JS = r"""
(function(){
  var tables=[].slice.call(document.querySelectorAll('table'));
  for (var i=0;i<tables.length;i++){
    var rows=[].slice.call(tables[i].rows);
    if(rows.length<5) continue;
    var text=function(r){return [].slice.call(r.cells).map(function(c){
      return (c.innerText||'').replace(/\s+/g,' ').trim();});};
    for (var h=0; h<Math.min(3, rows.length); h++){
      var head=text(rows[h]);
      if(head.indexOf('Monday')<0||head.indexOf('Timing')<0) continue;
      return JSON.stringify(rows.slice(h).map(text));
    }
  }
  return null;
})()
"""
MESSAGES_JS = r"""
(function(){
  var out=[], seen={};
  [].slice.call(document.querySelectorAll('*')).forEach(function(e){
    if(e.children.length>15) return;
    var t=(e.innerText||'').replace(/\s+/g,' ').trim();
    if(t.length<12||t.length>600) return;
    var m=t.match(/\([A-Z][a-z]{2} \d{1,2}, \d{4}\)/g);
    if(!m||m.length!==1) return;
    if(t.search(/\([A-Z][a-z]{2} \d{1,2}, \d{4}\)/)<4) return;
    if(seen[t]) return; seen[t]=1;
    out.push(t);
  });
  return JSON.stringify(out.slice(0,40));
})()
"""
OPEN_MESSAGES_JS = r"""
(function(){
  var el=[].slice.call(document.querySelectorAll('*')).find(function(e){
    return !e.children.length &&
      /^(Message|Messages|My Messages)$/.test((e.innerText||'').trim());});
  if(!el) return null;
  var r=el.getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});
})()
"""
MARKS_JS = r"""
(function(){
  var out=[];
  [].slice.call(document.querySelectorAll('*')).forEach(function(e){
    if(e.children.length) return;
    var t=(e.innerText||'').trim();
    if(!/^\d{1,2}$/.test(t)) return;
    var st=getComputedStyle(e);
    var week='';var p=e;
    for(var i=0;i<8&&p;i++){
      var m=(p.innerText||'').match(/Week \d+ \(([^)]*)\)/);
      if(m){week=m[1];break;} p=p.parentElement;}
    out.push({n:t, bg:st.backgroundColor, bd:st.borderTopColor,
              bw:parseFloat(st.borderTopWidth)||0, week:week});
  });
  return JSON.stringify(out);
})()
"""


def _to24(span):
    m = re.match(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s*(AM|PM)", span.strip())
    if not m:
        return None
    mer = m.group(5)

    def fix(h):
        return h + 12 if (mer == "PM" and h < 9) else h
    return f"{fix(int(m.group(1))):02d}:{m.group(2)}", f"{fix(int(m.group(3))):02d}:{m.group(4)}"


def _cell(text):
    if not text or "Project Work" in text:
        return None
    code = re.search(r"C:([A-Za-z]+\d+)", text)
    room = re.search(r"R:\s*(.+?)\s*/\s*S:", text)
    group = re.search(r"G:(\S+)", text)
    return {"type": admit("kind", text.split("/", 1)[0].strip()),
            "code": admit("course", code.group(1) if code else None),
            "room": admit("room", room.group(1) if room else None),
            "group": admit("group", group.group(1) if group else None)}


def parse_timetable(rows):
    header = rows[0]
    index = {d: header.index(d) for d in DAYS if d in header}
    slots = []
    for row in rows[1:]:
        span = next((_to24(c) for c in row[:3] if _to24(c)), None)
        if not span:
            continue
        per_day = {}
        for day, col in index.items():
            parsed = _cell(row[col]) if col < len(row) else None
            if parsed is None or not parsed["code"] or not parsed["type"]:
                continue
            parsed["room"] = parsed["room"] or "unknown"
            parsed["group"] = parsed["group"] or "?"
            per_day[day] = parsed
        slots.append((span, per_day))
    sessions = []
    for day in DAYS:
        run = None
        for (start, end), per_day in slots:
            found = per_day.get(day)
            key = (found["code"], found["type"], found["group"]) if found else None
            if run and key == run["_key"] and run["end"] == start:
                run["end"] = end
                if found["room"] not in run["rooms"]:
                    run["rooms"].append(found["room"])
                continue
            if run:
                sessions.append(run)
                run = None
            if found:
                run = {"_key": key, "day": SHORT[day], "start": start, "end": end,
                       "code": found["code"], "type": found["type"], "group": found["group"],
                       "rooms": [found["room"]]}
        if run:
            sessions.append(run)
    for s in sessions:
        s["room"] = " / ".join(s.pop("rooms"))
        s.pop("_key")
    return sessions


def parse_attendance(text):
    out = {}
    for code, pct in re.findall(r"\b([A-Z]{2,4}\d{3,4})\b\s*\n\s*(\d{1,3})%", text):
        c, p = admit("course", code), admit_int(pct, 0, 100)
        if c and p is not None:
            out[c] = p
    overall = re.search(r"Attendance\s*\n+\s*(\d{1,3})%", text)
    if overall:
        v = admit_int(overall.group(1), 0, 100)
        if v is not None:
            out["overall"] = v
    return out


def parse_messages(cards):
    out, seen = [], set()
    for card in cards:
        text = " ".join(str(card).split())
        found = re.search(r"\(([A-Z][a-z]{2}) (\d{1,2}), (\d{4})\)", text)
        if not found or found.start() < 4:
            continue
        head = text[:found.start()].strip()
        if head.startswith("By "):
            continue
        title, sender = (head.rsplit(" By ", 1) if " By " in head else (head, ""))
        title, sender = title.strip()[:140], sender.strip()[:60]
        if not title or title.startswith("By "):
            continue
        month = MONTHS.get(found.group(1))
        if not month:
            continue
        try:
            iso = date(int(found.group(3)), month, int(found.group(2))).isoformat()
        except ValueError:
            continue
        fp = fingerprint(f"{title}|{sender}|{iso}")
        if fp in seen:
            continue
        seen.add(fp)
        out.append({"title": title, "sender": sender, "date": iso, "fingerprint": fp,
                    "category": "class" if CLASS_WORDS.search(title) else "general"})
    return out


def _month_of(week_label, day):
    names = [MONTHS[m] for m in re.findall(r"[A-Z][a-z]{2,3}", week_label) if m in MONTHS]
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    try:
        return names[1] if int(day) <= 15 else names[0]
    except (TypeError, ValueError):
        return None


def parse_marks(cells, year):
    marked = {}
    for c in cells:
        kind = MARKS.get(c.get("bg")) or (MARKS.get(c.get("bd")) if c.get("bw", 0) >= 1 else None)
        if not kind:
            continue
        month = _month_of(c.get("week", ""), c.get("n"))
        day = admit_int(c.get("n"), 1, 31)
        if not month or day is None:
            continue
        stamp = admit("isodate", f"{year if month >= 8 else year + 1}-{month:02d}-{day:02d}")
        if stamp:
            marked.setdefault(kind, [])
            if stamp not in marked[kind]:
                marked[kind].append(stamp)
    for v in marked.values():
        v.sort()
    return marked


def open_spa(browser, page):
    routes = {"dashboard": "dashboard", "calendar": "dashboard/calendar"}
    tail = routes[page].rsplit("/", 1)[-1]
    for _ in range(3):
        browser.goto(SPA + routes[page], budget=60)
        deadline = time.time() + 25
        while time.time() < deadline:
            landed = browser.url()
            if "studentums.lpu.in" in landed and tail in landed:
                time.sleep(2)
                return
            time.sleep(2)
    raise UmsError(f"the {page} hand-off did not land; the session may have expired")


def _read_spa(browser):
    attendance, messages, marked = {}, [], {}
    try:
        open_spa(browser, "dashboard")
        attendance = parse_attendance(browser.text())
        coords = browser.js(OPEN_MESSAGES_JS)
        if coords:
            pt = json.loads(coords)
            browser.click(pt["x"], pt["y"])
            time.sleep(4)
        messages = parse_messages(json.loads(browser.js(MESSAGES_JS) or "[]"))
    except UmsError:
        pass
    try:
        open_spa(browser, "calendar")
        today = date.today()
        year = today.year if today.month >= 8 else today.year - 1
        marked = parse_marks(json.loads(browser.js(MARKS_JS) or "[]"), year)
    except UmsError:
        pass
    return attendance, messages, marked


def read_all(browser):
    browser.goto(TIMETABLE)
    grid = browser.js(GRID_JS)
    if not grid:
        raise UmsError("the timetable report did not render a weekly grid")
    sessions = parse_timetable(json.loads(grid))
    if not sessions:
        raise UmsError("read zero classes; refusing to store an empty week over a good one")
    attendance, messages, marked = _read_spa(browser)
    return {"at": datetime.now().isoformat(timespec="seconds"), "sessions": sessions,
            "attendance": attendance, "messages": messages, "marked": marked}


def _skey(s):
    return f"{s['day']} {s['start']}"


def changes(old, new):
    if not old:
        return []
    out = []
    before = {_skey(s): s for s in old.get("sessions", [])}
    after = {_skey(s): s for s in new.get("sessions", [])}
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key), after.get(key)
        if was and not now:
            out.append(f"CANCELLED: {key} {was['code']}")
        elif now and not was:
            out.append(f"NEW: {key} {now['code']} {now['type'].lower()} in {now['room']}")
        elif was.get("room") != now.get("room"):
            out.append(f"MOVED: {key} {now['code']}: {was['room']} -> {now['room']}")
        elif was.get("code") != now.get("code") or was.get("type") != now.get("type"):
            out.append(f"{key}: {was['code']} -> {now['code']}")
    ba, aa = old.get("attendance", {}), new.get("attendance", {})
    for code in sorted(set(ba) | set(aa)):
        if code != "overall" and ba.get(code) != aa.get(code) and code in ba and code in aa:
            out.append(f"attendance {code} {ba[code]}% -> {aa[code]}%")
    seen = {m["fingerprint"] for m in old.get("messages", [])}
    for m in new.get("messages", []):
        if m["fingerprint"] not in seen:
            tag = "NOTICE (class): " if m["category"] == "class" else "NOTICE: "
            out.append(tag + m["title"])
    return out


def _esc(s):
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _term_window(snap):
    today = date.today()
    boundaries = sorted(date.fromisoformat(d) for d in snap.get("marked", {}).get("term-boundary", []))
    anchor = today - timedelta(days=today.weekday())
    end = anchor + timedelta(weeks=16)
    for i in range(0, len(boundaries) - 1, 2):
        if boundaries[i] <= today <= boundaries[i + 1]:
            anchor, end = boundaries[i], boundaries[i + 1]
            break
    holidays = [date.fromisoformat(d) for d in snap.get("marked", {}).get("holiday", [])]
    return anchor, end, holidays


def write_ics(snap, tasks):
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    anchor, end, holidays = _term_window(snap)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//campus//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:LPU timetable (campus)"]
    for s in snap.get("sessions", []):
        wd = DAYNUM[s["day"]]
        first = anchor + timedelta(days=(wd - anchor.weekday()) % 7)
        sc, ec = s["start"].replace(":", "") + "00", s["end"].replace(":", "") + "00"
        ex = [h.strftime("%Y%m%d") + "T" + sc for h in holidays
              if h.weekday() == wd and first <= h <= end]
        lines += ["BEGIN:VEVENT",
                  f"UID:{_session_uid(s)}",
                  f"DTSTAMP:{stamp}", f"DTSTART:{first.strftime('%Y%m%d')}T{sc}",
                  f"DTEND:{first.strftime('%Y%m%d')}T{ec}",
                  f"RRULE:FREQ=WEEKLY;BYDAY={BYDAY[s['day']]};UNTIL={end.strftime('%Y%m%d')}T235959"]
        lines += [f"EXDATE:{e}" for e in ex]
        lines += [f"SUMMARY:{_esc(s['code'] + ' ' + s['type'].lower() + ' (' + s['room'] + ')')}",
                  "END:VEVENT"]
    for kind, label in (("mid-term-test", "Mid Term Test"), ("holiday", "Holiday")):
        for d in snap.get("marked", {}).get(kind, []):
            stampd = d.replace("-", "")
            nxt = (date.fromisoformat(d) + timedelta(days=1)).strftime("%Y%m%d")
            lines += ["BEGIN:VEVENT", f"UID:campus-{kind}-{stampd}@campus", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{stampd}", f"DTEND;VALUE=DATE:{nxt}",
                      f"SUMMARY:{_esc(label)}", "TRANSP:TRANSPARENT", "END:VEVENT"]
    for t in tasks:
        try:
            when = datetime.fromisoformat(t["when"])
        except ValueError:
            continue
        lines += ["BEGIN:VEVENT", f"UID:campus-task-{t['id']}@campus", f"DTSTAMP:{stamp}",
                  f"DTSTART:{when.strftime('%Y%m%dT%H%M%S')}",
                  f"DTEND:{(when + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')}",
                  f"SUMMARY:{_esc(t['text'])}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    HOME.mkdir(parents=True, exist_ok=True)
    ICS.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _google_token_path(module):
    return HOME / f"google_{module}.json"


def _google_post(url, params, attempts=3):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()), None
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = {}
            return None, body.get("error", str(e.code))
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            if attempt == attempts - 1:
                return None, str(e)
            time.sleep(1.5 * (attempt + 1))


def _google_api(access_token, url, method="GET", body=None, attempts=3):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return (json.loads(raw) if raw else {}), r.status
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read()), e.code
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}, e.code
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            if attempt == attempts - 1:
                return {"error": str(e)}, 0
            time.sleep(1.5 * (attempt + 1))


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.oauth_result = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>campus is connected. You can close this tab.</body></html>")

    def log_message(self, *args):
        pass


def google_enable(module):
    scope = GOOGLE_SCOPES.get(module)
    if not scope:
        print(f"no such module: {module} (try: calendar, gmail)")
        return False
    if module == "gmail":
        print("campus will read your Gmail subject lines (not full messages) looking for")
        print("fee/exam/placement-related mail, and send MATCHED subject lines to your desktop")
        print("and Telegram bot, the same way it already does for UMS notices.")
    server = http.server.HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    server.oauth_result = None
    server.timeout = 5
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}"
    auth_url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": scope, "access_type": "offline", "prompt": "consent",
    })
    print(f"\nOpen this link and approve access, then come back here:\n{auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    deadline = time.time() + 300
    while server.oauth_result is None and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if server.oauth_result is None:
        print(f"{module} sign-in timed out — run 'python campus.py enable {module}' to try again.")
        return False
    if "error" in server.oauth_result:
        print(f"{module} sign-in failed: {server.oauth_result['error'][0]}")
        return False
    code = (server.oauth_result.get("code") or [None])[0]
    if not code:
        print(f"{module} sign-in failed: no code returned")
        return False
    tok, err = _google_post(GOOGLE_TOKEN_URL, {
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    if err or not tok.get("refresh_token"):
        print(f"{module} sign-in failed: {err or 'no refresh token returned'}")
        return False
    existing = load(_google_token_path(module), {}) or {}
    existing["refresh_token"] = tok["refresh_token"]
    save(_google_token_path(module), existing)
    print(f"{module} connected.")
    return True


def google_disable(module):
    if module not in GOOGLE_SCOPES:
        print(f"no such module: {module} (try: calendar, gmail)")
        return False
    path = _google_token_path(module)
    tok = load(path, None)
    revoked = True
    if tok and tok.get("refresh_token"):
        _, err = _google_post(GOOGLE_REVOKE_URL, {"token": tok["refresh_token"]})
        revoked = err is None
    removed = path.exists()
    path.unlink(missing_ok=True)
    if not removed:
        print(f"{module} wasn't connected.")
    elif revoked:
        print(f"{module} disconnected.")
    else:
        print(f"{module} disconnected locally, but Google may not have confirmed the revoke —")
        print("you can also remove it yourself at myaccount.google.com/permissions.")
    return removed


def google_permissions():
    for module in GOOGLE_SCOPES:
        connected = bool(load(_google_token_path(module), None))
        print(f"{module}: {'connected' if connected else 'not connected'}")


def _google_access_token(module):
    tok = load(_google_token_path(module), None)
    if not tok or not tok.get("refresh_token"):
        return None
    resp, err = _google_post(GOOGLE_TOKEN_URL, {
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    })
    if err in ("invalid_grant", "unauthorized_client"):
        _google_token_path(module).unlink(missing_ok=True)
        notify(f"{module} disconnected",
               f"Your {module} connection expired or was revoked — "
               f"run 'python campus.py enable {module}' to reconnect.")
        return None
    return resp.get("access_token") if resp else None


def _gcal_event_id(uid):
    return hashlib.sha1(uid.encode()).hexdigest()


def _session_uid(s):
    return f"campus-{s['day'].lower()}-{s['start'].replace(':', '')}-{s['code']}@campus"


def _rrule_until_utc(end_date):
    local_end = datetime.combine(end_date, datetime.min.time()) + timedelta(hours=23, minutes=59, seconds=59)
    return (local_end - GOOGLE_TZ_OFFSET).strftime("%Y%m%dT%H%M%SZ")


def _gcal_session_event(s, anchor, end, holidays):
    wd = DAYNUM[s["day"]]
    first = anchor + timedelta(days=(wd - anchor.weekday()) % 7)
    sc = s["start"].replace(":", "") + "00"
    ex = [h for h in holidays if h.weekday() == wd and first <= h <= end]
    recurrence = [f"RRULE:FREQ=WEEKLY;BYDAY={BYDAY[s['day']]};UNTIL={_rrule_until_utc(end)}"]
    recurrence += [f"EXDATE;TZID={GOOGLE_TZ}:{h.strftime('%Y%m%d')}T{sc}" for h in ex]
    return {
        "id": _gcal_event_id(_session_uid(s)),
        "summary": s["code"] + " " + s["type"].lower() + " (" + s["room"] + ")",
        "start": {"dateTime": f"{first.isoformat()}T{s['start']}:00", "timeZone": GOOGLE_TZ},
        "end": {"dateTime": f"{first.isoformat()}T{s['end']}:00", "timeZone": GOOGLE_TZ},
        "recurrence": recurrence,
    }


def _gcal_allday_event(kind, label, d):
    stampd = d.replace("-", "")
    nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    uid = f"campus-{kind}-{stampd}@campus"
    return {"id": _gcal_event_id(uid), "summary": label,
            "start": {"date": d}, "end": {"date": nxt}, "transparency": "transparent"}


def _gcal_task_event(t):
    when = datetime.fromisoformat(t["when"])
    end = when + timedelta(hours=1)
    uid = f"campus-task-{t['id']}@campus"
    return {"id": _gcal_event_id(uid), "summary": t["text"],
            "start": {"dateTime": when.isoformat(), "timeZone": GOOGLE_TZ},
            "end": {"dateTime": end.isoformat(), "timeZone": GOOGLE_TZ}}


def _gcal_calendar_id(access):
    tok = load(_google_token_path("calendar"), {})
    cal_id = tok.get("calendar_id")
    if cal_id:
        _, status = _google_api(access, f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}")
        if status == 200:
            return cal_id
    body, status = _google_api(access, "https://www.googleapis.com/calendar/v3/calendars",
                                "POST", {"summary": "campus (LPU timetable)"})
    if status not in (200, 201):
        print(f"calendar: couldn't create the campus calendar (status {status})")
        return None
    tok["calendar_id"] = body["id"]
    save(_google_token_path("calendar"), tok)
    return tok["calendar_id"]


def _gcal_upsert(access, cal_id, ev):
    base = f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
    body, status = _google_api(access, base, "POST", ev)
    if status in (200, 201):
        return
    if status == 409:
        body, status = _google_api(access, f"{base}/{ev['id']}", "PUT", ev)
        if status in (200, 201):
            return
    msg = body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
    print(f"calendar: couldn't sync '{ev.get('summary', '?')}' (status {status}: {msg})")


def _gcal_delete_event(access, cal_id, eid):
    _google_api(access, f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events/{eid}", "DELETE")


def calendar_sync(old, new, tasks):
    access = _google_access_token("calendar")
    if not access:
        return
    cal_id = _gcal_calendar_id(access)
    if not cal_id:
        return
    anchor, end, holidays = _term_window(new)
    events = [_gcal_session_event(s, anchor, end, holidays) for s in new.get("sessions", [])]
    for kind, label in (("mid-term-test", "Mid Term Test"), ("holiday", "Holiday")):
        events += [_gcal_allday_event(kind, label, d) for d in new.get("marked", {}).get(kind, [])]
    for t in tasks:
        try:
            events.append(_gcal_task_event(t))
        except ValueError:
            continue
    for ev in events:
        _gcal_upsert(access, cal_id, ev)
    if old:
        gone = {_skey(s): s for s in old.get("sessions", [])}.keys() - {_skey(s) for s in new.get("sessions", [])}
        by_key = {_skey(s): s for s in old.get("sessions", [])}
        for key in gone:
            _gcal_delete_event(access, cal_id, _gcal_event_id(_session_uid(by_key[key])))


def _gcal_delete_calendar():
    access = _google_access_token("calendar")
    tok = load(_google_token_path("calendar"), {})
    cal_id = tok.get("calendar_id")
    if access and cal_id:
        _google_api(access, f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}", "DELETE")


def _gmail_important(subject):
    return bool(IMPORTANT_MAIL_PATTERN.search(subject))


def gmail_scan():
    access = _google_access_token("gmail")
    if not access:
        return []
    tok = load(_google_token_path("gmail"), {})
    since = tok.get("last_checked")
    query = f"in:inbox after:{since}" if since else "in:inbox newer_than:1d"
    seen_list = list(tok.get("seen", []))
    seen_set = set(seen_list)
    found = []
    page_token = None
    for _ in range(5):
        params = {"q": query, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        url = "https://www.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(params)
        listing, status = _google_api(access, url)
        if status != 200:
            print(f"gmail: couldn't list messages (status {status})")
            break
        for m in listing.get("messages", []):
            meta_url = ("https://www.googleapis.com/gmail/v1/users/me/messages/" + m["id"] +
                        "?format=metadata&metadataHeaders=Subject")
            msg, mstatus = _google_api(access, meta_url)
            if mstatus != 200:
                continue
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "")
            fp = fingerprint(subject)
            if fp in seen_set:
                continue
            seen_set.add(fp)
            seen_list.append(fp)
            if _gmail_important(subject):
                found.append(subject)
        page_token = listing.get("nextPageToken")
        if not page_token:
            break
    tok["seen"] = seen_list[-200:]
    tok["last_checked"] = int(time.time())
    save(_google_token_path("gmail"), tok)
    return found


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
        elif system == "Darwin":
            script = (f'display notification "{_applescript_escape(message)}" '
                      f'with title "{_applescript_escape(title)}"')
            subprocess.run(["osascript", "-e", script], timeout=10, check=False)
        elif system == "Windows":
            ps = ("$ErrorActionPreference='SilentlyContinue';"
                  "Add-Type -AssemblyName System.Windows.Forms;"
                  "$n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                  "$n.ShowBalloonTip(10000,$env:CAMPUS_NOTIFY_TITLE,$env:CAMPUS_NOTIFY_MSG,"
                  "[System.Windows.Forms.ToolTipIcon]::Info);Start-Sleep -Seconds 6;$n.Dispose()")
            env = dict(os.environ, CAMPUS_NOTIFY_TITLE=title, CAMPUS_NOTIFY_MSG=message)
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           timeout=15, check=False, env=env)
    except Exception:
        pass


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
        except Exception:
            if attempt == 2:
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
        if chat.get("id") is not None:
            return str(chat["id"])
    return None


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


def _redirect_to_log_if_headless():
    try:
        if sys.stdout.isatty():
            return
    except (AttributeError, ValueError):
        pass
    HOME.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log


def _autostart_desktop_path():
    return Path.home() / ".config" / "autostart" / "campus-agent.desktop"


def _autostart_plist_path():
    return Path.home() / "Library" / "LaunchAgents" / "dev.campus.agent.plist"


def _autostart_task_name():
    return f"campus-{getpass.getuser()}"


def _desktop_quote(value):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{escaped}"'


def autostart_enable():
    if not load(CREDS, None):
        print("save your login first (run campus.py and say yes to saving) before enabling autostart.")
        return False
    python, script = sys.executable, str(Path(__file__).resolve())
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
        tr = f'"{python}" "{script}"'
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


def credentials():
    env_user, env_pass = os.environ.get("CAMPUS_USER"), os.environ.get("CAMPUS_PASSWORD")
    if env_user and env_pass:
        return env_user.strip(), env_pass.strip()
    saved = load(CREDS, None)
    if saved and saved.get("user") and saved.get("password"):
        return saved["user"], saved["password"]
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
            autostart_enable()
    else:
        print("  not saved — it'll ask again next run.")
    return user, password


def setup_telegram():
    if os.environ.get("CAMPUS_TELEGRAM_TOKEN") or os.environ.get("CAMPUS_USER"):
        return
    cfg = load(CONFIG, {})
    if cfg.get("telegram_token"):
        return
    print("\nWant alerts on your phone via a Telegram bot? (desktop pop-ups work regardless.)")
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


def add_reminder(text, when_iso):
    tasks = load(TASKS, [])
    tasks.append({"id": fingerprint(text + when_iso)[:8], "text": text, "when": when_iso,
                  "notified": False})
    save(TASKS, tasks)
    print(f"got it — will remind you before {when_iso}: {text}")
    snap = load(STATE, None)
    if snap:
        write_ics(snap, tasks)


def due_reminders():
    tasks = load(TASKS, [])
    now = datetime.now()
    fired, changed = [], False
    for t in tasks:
        if t.get("notified"):
            continue
        try:
            when = datetime.fromisoformat(t["when"])
        except ValueError:
            continue
        if when - timedelta(minutes=INTERVAL_MIN + 15) <= now:
            fired.append(t)
            t["notified"] = True
            changed = True
    if changed:
        save(TASKS, tasks)
    return fired


def sync_once(user, password):
    with Browser() as br:
        login(br, user, password)
        return read_all(br)


def _merge_forward(old, new):
    if old:
        for k in ("attendance", "messages", "marked"):
            if not new.get(k) and old.get(k):
                new[k] = old[k]
    return new


def _record_failure(exc):
    n = load(FAILS, {"count": 0}).get("count", 0) + 1
    save(FAILS, {"count": n})
    if n == 3 or (n > 3 and n % 32 == 0):
        notify("campus can't log in", f"UMS login has failed {n} times in a row: {exc}\n"
               "Your password may have changed — run campus.py and re-enter it.")


def _record_success():
    FAILS.unlink(missing_ok=True)


def check(user, password):
    HOME.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 300:
        print("another campus sync is already in progress — skipping this cycle.")
        return []
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    try:
        old = load(STATE, None)
        new = _merge_forward(old, sync_once(user, password))
        diff = changes(old, new)
        save(STATE, new)
        tasks = load(TASKS, [])
        write_ics(new, tasks)
        calendar_on = bool(load(_google_token_path("calendar"), None))
        if diff:
            body = "\n".join(f"- {c}" for c in diff[:8])
            if not calendar_on:
                body += "\n\n(re-import campus.ics into Google Calendar to update it there too)"
            notify("UMS changed", body)
        for t in due_reminders():
            notify("Reminder", f"{t['text']} — due {t['when']}")
        if calendar_on:
            try:
                calendar_sync(old, new, tasks)
            except Exception as exc:
                print(f"calendar sync skipped: {exc}")
        if load(_google_token_path("gmail"), None):
            try:
                important_mail = gmail_scan()
                if important_mail:
                    notify("Important mail", "\n".join(f"- {s}" for s in important_mail[:8]))
            except Exception as exc:
                print(f"gmail scan skipped: {exc}")
        return diff
    finally:
        LOCK.unlink(missing_ok=True)


def run_once():
    user, password = credentials()
    setup_telegram()
    print("checking UMS…")
    diff = check(user, password)
    if not diff:
        print("no change")
    elif os.environ.get("CAMPUS_QUIET"):
        print(f"{len(diff)} change(s) — sent to your notifications")
    else:
        print("changes:\n" + "\n".join(f"- {c}" for c in diff))


def run_loop():
    print(f"campus is watching UMS every {INTERVAL_MIN} min. Ctrl+C to stop.")
    print(f"calendar file kept fresh at: {ICS}")
    user, password = credentials()
    setup_telegram()
    HOME.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    try:
        while True:
            stamp = datetime.now().strftime("%H:%M")
            try:
                diff = check(user, password)
                print(f"[{stamp}] {len(diff)} change(s):\n" + "\n".join(f"- {c}" for c in diff)
                      if diff else f"[{stamp}] no change")
                _record_success()
            except UmsError as exc:
                print(f"[{stamp}] sync skipped: {exc}")
                _record_failure(exc)
            except KeyboardInterrupt:
                print("\nstopped.")
                return
            try:
                time.sleep(INTERVAL_MIN * 60)
            except KeyboardInterrupt:
                print("\nstopped.")
                return
    finally:
        PIDFILE.unlink(missing_ok=True)


def bomb():
    print("This deletes EVERYTHING campus stored: your saved login, Telegram token,")
    print("the calendar file, reminders, the browser profile, and any Google Calendar /")
    print(f"Gmail connection — all of {HOME}.")
    if ask('Type DELETE to confirm: ').strip() != "DELETE":
        print("cancelled.")
        return
    try:
        if load(_google_token_path("calendar"), None):
            try:
                _gcal_delete_calendar()
            except Exception:
                pass
        for module in GOOGLE_SCOPES:
            try:
                google_disable(module)
            except Exception:
                pass
        autostart_disable()
        if PIDFILE.exists():
            try:
                os.kill(int(PIDFILE.read_text(encoding="utf-8").strip()), signal.SIGTERM)
                print("stopped the background campus process that was running.")
            except (OSError, ValueError):
                pass
    finally:
        if HOME.exists():
            shutil.rmtree(HOME, ignore_errors=True)
    print("done — campus wiped itself. Delete campus.py too if you're finished with it.")


def _parse_reminder(args):
    for take in (2, 1):
        if len(args) - take < 1:
            continue
        candidate = " ".join(args[-take:]).strip()
        norm = candidate.replace(" ", "T", 1) if "T" not in candidate else candidate
        try:
            when = datetime.fromisoformat(norm)
        except ValueError:
            continue
        text = " ".join(args[:-take]).strip()
        if text:
            return text, when.isoformat(timespec="minutes")
    return None, None


def usage():
    print(__doc__.strip())


def main(argv):
    try:
        import websocket
        websocket.create_connection
    except ImportError:
        print("campus needs one package that isn't installed: websocket-client")
        print("try:   pip install websocket-client")
        print("if that says 'externally-managed-environment', try:")
        print("       pip install --break-system-packages websocket-client")
        return 1
    if not argv:
        _redirect_to_log_if_headless()
        run_loop()
        return 0
    cmd = argv[0]
    if cmd == "once":
        run_once()
    elif cmd in ("remind", "add") and len(argv) >= 3:
        text, when_iso = _parse_reminder(argv[1:])
        if not text:
            print('use:  python campus.py remind "what to do" "2026-08-25 17:00"')
            return 2
        add_reminder(text, when_iso)
    elif cmd == "reminders":
        for t in load(TASKS, []):
            mark = "done" if t.get("notified") else "pending"
            print(f"{t['when']}  [{mark}]  {t['text']}")
    elif cmd == "chatid" and len(argv) >= 2:
        print(telegram_chat_id(argv[1]) or "no message found — send your bot 'hi' first, then retry")
    elif cmd == "enable" and len(argv) >= 2:
        google_enable(argv[1].lower())
    elif cmd == "disable" and len(argv) >= 2:
        google_disable(argv[1].lower())
    elif cmd == "permissions":
        google_permissions()
    elif cmd == "autostart":
        arg = argv[1].lower() if len(argv) >= 2 else None
        if arg is None:
            autostart_enable()
        elif arg == "off":
            autostart_disable()
        else:
            print('use:  python campus.py autostart        (turn on)')
            print('  or  python campus.py autostart off    (turn off)')
            return 2
    elif cmd == "bomb":
        bomb()
    else:
        usage()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except UmsError as exc:
        print(f"campus: {exc}")
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
