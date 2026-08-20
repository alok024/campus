#!/usr/bin/env python3
"""campus — a one-file agent that watches LPU UMS so you don't have to.

It logs into UMS every 45 minutes, notices when a class is cancelled or moved or a
new notice is posted, tells you, keeps a calendar file up to date, and reminds you
about things you ask it to remember. Everything runs on your own machine.

Run it:            python campus.py
Add a reminder:    python campus.py remind "submit DBMS assignment" 2026-08-25 17:00
List reminders:    python campus.py reminders
Sync once and exit:python campus.py once
Delete everything: python campus.py bomb

Needs: Python 3.9+, Google Chrome or Microsoft Edge, and one package:
    pip install websocket-client
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path(os.environ.get("CAMPUS_HOME") or (Path.home() / ".campus"))
STATE = HOME / "state.json"
CONFIG = HOME / "config.json"
CREDS = HOME / "creds.json"
TASKS = HOME / "reminders.json"
ICS = HOME / "campus.ics"
PROFILE = HOME / "browser"

PORTAL = "https://ums.lpu.in/lpuums/"
TIMETABLE = PORTAL + "Reports/frmStudentTimeTable.aspx"
SPA = PORTAL + "openapp.aspx?from=ums&toApp=nextproject&pagename="
INTERVAL_MIN = 45
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
        if "StudentDashboard" in browser.url():
            return
        if not browser.js("!!document.querySelector('input[type=password]')"):
            raise UmsError("the portal did not serve a login form")
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
    routes = {"dashboard": "dashboard",
              "calendar": "dashboard/calendar"}
    browser.goto(SPA + routes[page], budget=60)
    landed = browser.url()
    if "studentums.lpu.in" not in landed or routes[page].rsplit("/", 1)[-1] not in landed:
        raise UmsError(f"the {page} hand-off did not land; the session may have expired")


def read_all(browser):
    browser.goto(TIMETABLE)
    grid = browser.js(GRID_JS)
    if not grid:
        raise UmsError("the timetable report did not render a weekly grid")
    sessions = parse_timetable(json.loads(grid))

    open_spa(browser, "dashboard")
    attendance = parse_attendance(browser.text())
    coords = browser.js(OPEN_MESSAGES_JS)
    if coords:
        pt = json.loads(coords)
        browser.click(pt["x"], pt["y"])
        time.sleep(4)
    messages = parse_messages(json.loads(browser.js(MESSAGES_JS) or "[]"))

    open_spa(browser, "calendar")
    today = date.today()
    year = today.year if today.month >= 8 else today.year - 1
    marked = parse_marks(json.loads(browser.js(MARKS_JS) or "[]"), year)

    if not sessions:
        raise UmsError("read zero classes; refusing to store an empty week over a good one")
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


def write_ics(snap, tasks):
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    today = date.today()
    boundaries = sorted(date.fromisoformat(d) for d in snap.get("marked", {}).get("term-boundary", []))
    anchor = today - timedelta(days=today.weekday())
    end = anchor + timedelta(weeks=16)
    for i in range(0, len(boundaries) - 1, 2):
        if boundaries[i] <= today <= boundaries[i + 1]:
            anchor, end = boundaries[i], boundaries[i + 1]
            break
    holidays = [date.fromisoformat(d) for d in snap.get("marked", {}).get("holiday", [])]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//campus//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:LPU timetable (campus)"]
    for s in snap.get("sessions", []):
        wd = DAYNUM[s["day"]]
        first = anchor + timedelta(days=(wd - anchor.weekday()) % 7)
        sc, ec = s["start"].replace(":", "") + "00", s["end"].replace(":", "") + "00"
        ex = [h.strftime("%Y%m%d") + "T" + sc for h in holidays
              if h.weekday() == wd and first <= h <= end]
        lines += ["BEGIN:VEVENT",
                  f"UID:campus-{s['day'].lower()}-{s['start'].replace(':', '')}-{s['code']}@campus",
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


def notify(title, message):
    _desktop(title, message)
    cfg = load(CONFIG, {})
    if cfg.get("telegram_token") and cfg.get("telegram_chat"):
        _telegram(cfg["telegram_token"], cfg["telegram_chat"], title, message)


def _desktop(title, message):
    system = platform.system()
    try:
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", "--app-name=campus", "--", title, message],
                           timeout=10, check=False)
        elif system == "Darwin":
            safe = message.replace('"', "'")
            subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "campus"'],
                           timeout=10, check=False)
        elif system == "Windows":
            ps = ("$ErrorActionPreference='SilentlyContinue';"
                  "Add-Type -AssemblyName System.Windows.Forms;"
                  "$n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                  f"$n.ShowBalloonTip(10000,{json.dumps(title)},{json.dumps(message)},"
                  "[System.Windows.Forms.ToolTipIcon]::Info);Start-Sleep -Seconds 6;$n.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=15, check=False)
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
        return ask_secret(prompt)
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


def credentials():
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
    else:
        print("  not saved — it'll ask again next run.")
    return user, password


def setup_telegram():
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


def run_once(announce=True):
    user, password = credentials()
    setup_telegram()
    HOME.mkdir(parents=True, exist_ok=True)
    if announce:
        print("checking UMS…")
    new = sync_once(user, password)
    old = load(STATE, None)
    diff = changes(old, new)
    save(STATE, new)
    write_ics(new, load(TASKS, []))
    if diff:
        body = "\n".join(f"- {c}" for c in diff[:8])
        notify("UMS changed", body)
        print("changes:\n" + body)
    else:
        print("no change" if announce else "", end="" if not announce else "\n")
    return diff


def run_loop():
    print(f"campus is watching UMS every {INTERVAL_MIN} min. Ctrl+C to stop.")
    print(f"calendar file kept fresh at: {ICS}")
    user, password = credentials()
    setup_telegram()
    while True:
        try:
            new = sync_once(user, password)
            old = load(STATE, None)
            diff = changes(old, new)
            save(STATE, new)
            write_ics(new, load(TASKS, []))
            stamp = datetime.now().strftime("%H:%M")
            if diff:
                body = "\n".join(f"- {c}" for c in diff[:8])
                notify("UMS changed", body)
                print(f"[{stamp}] {len(diff)} change(s):\n" + body)
            else:
                print(f"[{stamp}] no change")
        except UmsError as exc:
            print(f"[{datetime.now():%H:%M}] sync skipped: {exc}")
        except KeyboardInterrupt:
            print("\nstopped.")
            return
        for t in due_reminders():
            notify("Reminder", f"{t['text']} — due {t['when']}")
            print(f"reminder fired: {t['text']}")
        try:
            time.sleep(INTERVAL_MIN * 60)
        except KeyboardInterrupt:
            print("\nstopped.")
            return


def bomb():
    print("This deletes EVERYTHING campus stored: your saved login, Telegram token,")
    print(f"the calendar file, reminders, and the browser profile — all of {HOME}.")
    if ask('Type DELETE to confirm: ').strip() != "DELETE":
        print("cancelled.")
        return
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
    if not argv:
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
