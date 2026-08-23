import json
import re
import time
from datetime import date, datetime, timedelta

from campus.paths import CLASS_WORDS, DAYNUM, DAYS, MARKS, MONTHS, PORTAL, SHORT, SPA, TIMETABLE, UmsError
from campus.util import admit, admit_int, fingerprint

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
    reached_form = False
    submitted = False
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
        reached_form = True
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
        submitted = True
        for _ in range(8):
            time.sleep(3)
            if "StudentDashboard" in browser.url():
                return
    if not reached_form:
        raise UmsError("the UMS login page never appeared — check your internet connection; "
                       "ums.lpu.in may also be slow or down right now")
    if not submitted:
        raise UmsError("the login form wouldn't accept the details typed into it — try again")
    raise UmsError("login was submitted but never reached the dashboard; check your reg number/password "
                   "(UMS forces a password change every 90 days) — ums.lpu.in may also be slow right now")


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

    def fix(h, is_end=False):
        if mer == "PM":
            return h if h == 12 else h + 12
        if h == 12:
            return 12 if is_end else 0
        return h
    return f"{fix(int(m.group(1))):02d}:{m.group(2)}", f"{fix(int(m.group(3)), True):02d}:{m.group(4)}"


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
    except UmsError as exc:
        print(f"dashboard read skipped: {exc}")
    try:
        open_spa(browser, "calendar")
        today = date.today()
        year = today.year if today.month >= 8 else today.year - 1
        marked = parse_marks(json.loads(browser.js(MARKS_JS) or "[]"), year)
    except UmsError as exc:
        print(f"calendar read skipped: {exc}")
    return attendance, messages, marked


def read_all(browser):
    browser.goto(TIMETABLE)
    grid = browser.js(GRID_JS)
    if not grid:
        raise UmsError("the timetable report did not render a weekly grid")
    time.sleep(1.5)
    grid2 = browser.js(GRID_JS)
    if grid2 and grid2 != grid:
        time.sleep(2)
        grid2 = browser.js(GRID_JS)
    grid = grid2 or grid
    sessions = parse_timetable(json.loads(grid))
    if not sessions:
        raise UmsError("read zero classes; refusing to store an empty week over a good one")
    attendance, messages, marked = _read_spa(browser)
    return {"at": datetime.now().isoformat(timespec="seconds"), "sessions": sessions,
            "attendance": attendance, "messages": messages, "marked": marked}


def _skey(s):
    return f"{s['day']} {s['start']}"


def _skey_order(key):
    day, start = key.split(" ", 1)
    return (DAYNUM[day], start)


def changes(old, new):
    if not old:
        return []
    out = []
    before = {_skey(s): s for s in old.get("sessions", [])}
    after = {_skey(s): s for s in new.get("sessions", [])}
    for key in sorted(set(before) | set(after), key=_skey_order):
        was, now = before.get(key), after.get(key)
        if was and not now:
            out.append(f"CANCELLED: {key} {was['code']}")
            continue
        if now and not was:
            out.append(f"NEW: {key} {now['code']} {now['type'].lower()} in {now['room']}")
            continue
        if was.get("room") != now.get("room"):
            out.append(f"MOVED: {key} {now['code']}: {was['room']} -> {now['room']}")
        if was.get("code") != now.get("code") or was.get("type") != now.get("type"):
            out.append(f"{key}: {was['code']} -> {now['code']}")
        if was.get("group") != now.get("group"):
            out.append(f"{key} {now['code']}: group {was.get('group')} -> {now.get('group')}")
        if was.get("end") != now.get("end"):
            out.append(f"{key} {now['code']}: now ends {now.get('end')} (was {was.get('end')})")
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
