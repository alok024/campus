from datetime import date, datetime, timedelta

from campus.paths import BYDAY, DAYNUM, HOME, ICS


def _esc(s):
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _session_uid(s):
    return f"campus-{s['day'].lower()}-{s['start'].replace(':', '')}-{s['code']}@campus"


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
             "METHOD:PUBLISH", "X-WR-CALNAME:LPU timetable (campus)",
             "BEGIN:VTIMEZONE", "TZID:Asia/Kolkata", "BEGIN:STANDARD",
             "DTSTART:19700101T000000", "TZOFFSETFROM:+0530", "TZOFFSETTO:+0530",
             "TZNAME:IST", "END:STANDARD", "END:VTIMEZONE"]
    for s in snap.get("sessions", []):
        wd = DAYNUM[s["day"]]
        first = anchor + timedelta(days=(wd - anchor.weekday()) % 7)
        sc, ec = s["start"].replace(":", "") + "00", s["end"].replace(":", "") + "00"
        ex = [h.strftime("%Y%m%d") + "T" + sc for h in holidays
              if h.weekday() == wd and first <= h <= end]
        lines += ["BEGIN:VEVENT",
                  f"UID:{_session_uid(s)}",
                  f"DTSTAMP:{stamp}", f"DTSTART;TZID=Asia/Kolkata:{first.strftime('%Y%m%d')}T{sc}",
                  f"DTEND;TZID=Asia/Kolkata:{first.strftime('%Y%m%d')}T{ec}",
                  f"RRULE:FREQ=WEEKLY;BYDAY={BYDAY[s['day']]};UNTIL={end.strftime('%Y%m%d')}T235959"]
        lines += [f"EXDATE;TZID=Asia/Kolkata:{e}" for e in ex]
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
                  f"DTSTART;TZID=Asia/Kolkata:{when.strftime('%Y%m%dT%H%M%S')}",
                  f"DTEND;TZID=Asia/Kolkata:{(when + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')}",
                  f"SUMMARY:{_esc(t['text'])}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    HOME.mkdir(parents=True, exist_ok=True)
    ICS.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
