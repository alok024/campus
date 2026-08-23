import hashlib
import http.client
import http.server
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta

from campus.ics import _term_window, _session_uid
from campus.paths import (
    BYDAY, DAYNUM, GOOGLE_AUTH_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REVOKE_URL,
    GOOGLE_SCOPES, GOOGLE_TOKEN_URL, GOOGLE_TZ, GOOGLE_TZ_OFFSET, HOME, IMPORTANT_MAIL_PATTERN,
)
from campus.storage import load, save
from campus.telegram import notify
from campus.util import fingerprint


def _google_token_path(module):
    return HOME / f"google_{module}.json"


def _retry_delay(http_error, attempt):
    retry_after = http_error.headers.get("Retry-After") if http_error.headers else None
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return 2 ** attempt


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
            if e.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(_retry_delay(e, attempt))
                continue
            return None, body.get("error", str(e.code))
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            if attempt == attempts - 1:
                return None, str(e)
            time.sleep(2 ** attempt)


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
                err_body = json.loads(e.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                err_body = {}
            if e.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(_retry_delay(e, attempt))
                continue
            return err_body, e.code
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            if attempt == attempts - 1:
                return {"error": str(e)}, 0
            time.sleep(2 ** attempt)


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
    csrf_state = secrets.token_urlsafe(24)
    auth_url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": scope, "access_type": "offline", "prompt": "consent", "state": csrf_state,
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
    returned_state = (server.oauth_result.get("state") or [None])[0]
    if returned_state != csrf_state:
        print(f"{module} sign-in failed: the callback didn't match this request "
              "(possible CSRF) — run it again")
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
        before_uids = {_session_uid(s) for s in old.get("sessions", [])}
        after_uids = {_session_uid(s) for s in new.get("sessions", [])}
        for uid in before_uids - after_uids:
            _gcal_delete_event(access, cal_id, _gcal_event_id(uid))


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
    ok = True
    for _ in range(5):
        params = {"q": query, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        url = "https://www.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(params)
        listing, status = _google_api(access, url)
        if status != 200:
            print(f"gmail: couldn't list messages (status {status})")
            ok = False
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
    if ok:
        tok["last_checked"] = int(time.time())
    save(_google_token_path("gmail"), tok)
    return found
