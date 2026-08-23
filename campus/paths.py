import os
import re
import threading
from datetime import timedelta
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
BOTFILE = HOME / "bot.json"
LOG = HOME / "campus.log"

PORTAL = "https://ums.lpu.in/lpuums/"
TIMETABLE = PORTAL + "Reports/frmStudentTimeTable.aspx"
SPA = PORTAL + "openapp.aspx?from=ums&toApp=nextproject&pagename="
INTERVAL_MIN = 45

GOOGLE_CLIENT_ID = os.environ.get(
    "CAMPUS_GOOGLE_CLIENT_ID",
    "665688308829-cettf0u20bkg2ju268nbhbhfoo4pv7hr.apps.googleusercontent.com")
GOOGLE_CLIENT_SECRET = os.environ.get(
    "CAMPUS_GOOGLE_CLIENT_SECRET", "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")
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

_SYNC_LOCK = threading.Lock()
_TASKS_LOCK = threading.Lock()


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
