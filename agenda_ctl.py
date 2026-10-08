#!/usr/bin/env python3
"""Agenda's calendar back end: three interchangeable sources behind one command.

  agenda_ctl.py fetch --out PATH        read the configured source, write the next
                                        seven days as the agenda's day.json
  agenda_ctl.py google-login            sign in to Google (browser, loopback, PKCE)
  agenda_ctl.py config-set              merge the JSON in $AGENDA_PATCH into the config
  agenda_ctl.py config-show             the config with every secret replaced by "set" and a
                                        command's arguments hidden (a script's token lives there)

Sources (config.json "source"):
  ics      a secret iCal address (any provider); parsed by fetch_ics.py
  google   the Google Calendar API, with the user's OWN OAuth client. Nothing is
           registered with or reviewed by Google on anyone's behalf: each person
           creates a client in their own Google Cloud project and signs in to it.
           Read-only scope. Standard library only.
  command  the user's own program. It prints the day.json document on stdout, or
           writes it to the path that replaces {out} in the command line. This is
           the open door for people who already have an agent or a sync of their own.

The config lives in ~/.config/omarchy/agenda/config.json, mode 0600, because it
holds credentials. Nothing here ever prints a credential.

Exit status: 0 on success; 1 with one line on stderr on any failure, leaving the
previous day.json in place.
"""
import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import http.server
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_ics  # noqa: E402  (same folder)

GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
# Overridable so the tests can stand a local server in for Google.
AUTH_URL = os.environ.get("AGENDA_GOOGLE_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
TOKEN_URL = os.environ.get("AGENDA_GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")
API_BASE = os.environ.get("AGENDA_GOOGLE_API", "https://www.googleapis.com/calendar/v3")
SECRET_KEYS = {"url", "client_secret", "refresh_token", "client_id"}
MAX_PAGES = 20  # of Google results; a server that never stops paging must not hold the widget


class AgendaError(Exception):
    """A failure whose message is safe to show the user."""


# ---------------------------------------------------------------------- config

def config_path():
    return os.environ.get("AGENDA_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".config", "omarchy", "agenda", "config.json")


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (ValueError, RecursionError):
        raise AgendaError("config.json is not valid JSON")
    except OSError as ex:
        raise AgendaError("cannot read config.json: {}".format(type(ex).__name__))
    return data if isinstance(data, dict) else {}


def section(cfg, name):
    """cfg[name] as a dict: a hand-edited config may hold a string or a list there."""
    node = cfg.get(name)
    return node if isinstance(node, dict) else {}


@contextlib.contextmanager
def config_lock():
    """Serialises read-modify-write of config.json between the settings form, an agent and a
    sign-in that finishes later: two writers otherwise lose each other's changes."""
    path = config_path() + ".lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def save_config(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 0600 from the first byte: the file is created private, never widened after.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def merge(base, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def hide_arguments(line):
    """The program of a command line and how many arguments follow: arguments are where a script's
    token ends up (`curl -H "X-Token: ..."`), so config-show does not print them."""
    try:
        argv = shlex.split(line)
    except ValueError:
        return "set"
    return "{} (+{} argument(s) hidden)".format(argv[0], len(argv) - 1) if argv else "set"


def redact(node, parent=""):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in SECRET_KEYS and v:
                out[k] = "set"
            elif parent == "command" and k == "line" and isinstance(v, str) and v:
                out[k] = hide_arguments(v)
            else:
                out[k] = redact(v, k)
        return out
    return node


def apply_patch(cfg, patch):
    """Merge a settings patch. A blank credential means "left empty in the form", not "erase it"; a
    blank anything else clears the key (an emptied watch path). A new Google client invalidates the
    old refresh token, which belongs to the previous client."""
    before = (section(cfg, "google").get("client_id"), section(cfg, "google").get("client_secret"))

    def walk(base, node):
        for key, value in node.items():
            if value == "":
                if key not in SECRET_KEYS:
                    base.pop(key, None)
            elif isinstance(value, dict):
                if not isinstance(base.get(key), dict):
                    base[key] = {}
                walk(base[key], value)
            else:
                base[key] = value
    walk(cfg, patch)
    g = section(cfg, "google")
    if (g.get("client_id"), g.get("client_secret")) != before and "refresh_token" not in section(patch, "google"):
        g.pop("refresh_token", None)


# ---------------------------------------------------------------------- shared

def window(tz, days=7):
    now = dt.datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now, start, start + dt.timedelta(days=days)


def document(by_date, source, calendars, now, tz, days=7):
    return {
        "as_of": now.astimezone(tz).isoformat(timespec="seconds"),
        "window_days": days,
        "source": source,
        "calendars": calendars,
        "events_by_date": by_date,
    }


def write_document(doc, out):
    # A temp name of its own: two fetches racing for one path must not trample each other's file.
    folder = os.path.dirname(os.path.abspath(out))
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".day-", suffix=".tmp")
    except OSError as ex:
        raise AgendaError("cannot write {}: {}".format(os.path.basename(out), type(ex).__name__))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, allow_nan=False)  # NaN is not JSON: the shell would refuse the file
        os.replace(tmp, out)
    except (OSError, ValueError) as ex:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise AgendaError("cannot write {}: {}".format(os.path.basename(out), type(ex).__name__))


def validate_document(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("events_by_date"), dict):
        raise AgendaError('the result has no "events_by_date"')
    for day, events in doc["events_by_date"].items():
        if not isinstance(events, list):
            raise AgendaError('"events_by_date" must map dates to lists')
        doc["events_by_date"][day] = events = [e for e in events if isinstance(e, dict)]
        for e in events:
            if "error" not in e and not e.get("start"):
                raise AgendaError("an event has no \"start\"")
    doc.setdefault("as_of", dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    return doc


# ------------------------------------------------------------------------- ics

def fetch_ics_source(cfg, out):
    url = str(section(cfg, "ics").get("url", "")).strip()
    if not url:
        raise AgendaError("the iCal address is missing")
    try:
        text = fetch_ics.read_source(url)
        if "BEGIN:VCALENDAR" not in text:
            raise AgendaError("the response is not an iCal calendar")
        tz = fetch_ics.local_zone()
        payload, unsupported = fetch_ics.build(text, dt.datetime.now(tz), 7, tz)
    except AgendaError:
        raise
    except urllib.error.HTTPError as ex:
        raise AgendaError("could not read the calendar: the server answered {}".format(ex.code))
    except urllib.error.URLError as ex:
        raise AgendaError("could not read the calendar: {}".format(ex.reason))
    except Exception as ex:
        # The address is a credential, and exceptions such as InvalidURL quote
        # parts of it (a bare path does not match the full URL, so replacing
        # the URL is not enough). Unknown failures show their type only.
        raise AgendaError("could not read the calendar: {}".format(type(ex).__name__))
    if unsupported:
        sys.stderr.write("{} event(s) with an unsupported recurrence rule are shown only on their first date\n".format(unsupported))
    return payload


# --------------------------------------------------------------------- command

MAX_COMMAND_BYTES = 25 * 1024 * 1024


def run_command(argv):
    """Run argv without a shell in its own process group, so a timeout takes the script's own
    children with it. Returns (returncode, stdout bytes, stderr bytes)."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except FileNotFoundError:
        raise AgendaError("program not found: {}".format(argv[0]))
    except OSError as ex:
        raise AgendaError("cannot run the command: {}".format(type(ex).__name__))
    try:
        stdout, stderr = proc.communicate(timeout=25)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise AgendaError("the command took longer than 25 s")
    return proc.returncode, stdout, stderr


def fetch_command_source(cfg, out):
    line = str(section(cfg, "command").get("line", "")).strip()
    if not line:
        raise AgendaError("the command is missing")
    try:
        argv = shlex.split(line)
    except ValueError as ex:
        raise AgendaError("malformed command: {}".format(ex))
    if not argv:
        raise AgendaError("the command is missing")
    uses_file = any("{out}" in part for part in argv)
    tmp_out = None
    if uses_file:
        # A private file of its own, created empty: never a stale one from an earlier crash.
        try:
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            fd, tmp_out = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(out)), prefix=".day-command-")
            os.close(fd)
        except OSError as ex:
            raise AgendaError("cannot prepare the output file: {}".format(type(ex).__name__))
        argv = [part.replace("{out}", tmp_out) for part in argv]
    try:
        code, stdout, stderr = run_command(argv)
        if code != 0:
            tail = (stderr.decode("utf-8", "replace").strip().splitlines() or [""])[-1][:200]
            raise AgendaError("the command exited with code {}{}".format(code, ": " + tail if tail else ""))
        try:
            if uses_file:
                with open(tmp_out, "rb") as f:
                    raw = f.read(MAX_COMMAND_BYTES + 1)
            else:
                raw = stdout
            if len(raw) > MAX_COMMAND_BYTES:
                raise AgendaError("the command printed more than 25 MB")
            doc = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
            raise AgendaError("the command did not produce valid JSON")
    finally:
        if tmp_out:
            with contextlib.suppress(OSError):
                os.unlink(tmp_out)
    return validate_document(doc)


# ---------------------------------------------------------------------- google

def http_json(url, data=None, headers=None, method=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        try:
            detail = json.loads(ex.read().decode("utf-8"))
        except Exception:
            detail = {}
        raise AgendaError(describe_google_error(ex.code, detail))
    except urllib.error.URLError as ex:
        raise AgendaError("cannot reach Google: {}".format(ex.reason))
    except (OSError, ValueError, RecursionError) as ex:  # a timeout, an HTML page where JSON was due
        raise AgendaError("Google did not answer correctly: {}".format(type(ex).__name__))


def describe_google_error(code, detail):
    err = detail.get("error")
    err = err.get("message") if isinstance(err, dict) else err
    if err == "invalid_grant":
        return ("the Google session expired or was revoked: sign in again "
                "(if your app is in Testing mode, it expires after 7 days)")
    if err == "invalid_client":
        return "the client ID or secret is not valid"
    return "Google answered {}{}".format(code, ": " + str(err)[:120] if err else "")


def google_access_token(g):
    for key in ("client_id", "client_secret", "refresh_token"):
        if not g.get(key):
            raise AgendaError("sign in with Google first")
    tok = http_json(TOKEN_URL, {
        "client_id": g["client_id"], "client_secret": g["client_secret"],
        "refresh_token": g["refresh_token"], "grant_type": "refresh_token"})
    if not isinstance(tok, dict) or not tok.get("access_token"):
        raise AgendaError("Google did not return an access token")
    return tok["access_token"]


def google_get(path, token, params):
    qs = urllib.parse.urlencode(params, doseq=True)
    return http_json("{}{}?{}".format(API_BASE, path, qs), headers={"Authorization": "Bearer " + token})


def fetch_google_source(cfg, out):
    g = section(cfg, "google")
    token = google_access_token(g)
    tz = fetch_ics.local_zone()
    now, start, end = window(tz)
    tzname = getattr(tz, "key", None) or "UTC"

    wanted = g.get("calendars") or []
    cal_ids = [c for c in ([wanted] if isinstance(wanted, str) else wanted) if isinstance(c, str) and c]
    if not cal_ids:
        # Everything the person has switched on in Google Calendar's own list.
        items, page = [], None
        for _ in range(MAX_PAGES):
            params = {"minAccessRole": "reader", "maxResults": 250}
            if page:
                params["pageToken"] = page
            data = google_get("/users/me/calendarList", token, params)
            items += data.get("items", [])
            page = data.get("nextPageToken")
            if not page:
                break
        cal_ids = [c["id"] for c in items if isinstance(c, dict) and isinstance(c.get("id"), str)
               and (c.get("selected") or c.get("primary"))]

    by_date, failures = {}, []
    for cid in cal_ids:
        try:
            page = None
            for _ in range(MAX_PAGES):
                params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                          "orderBy": "startTime", "maxResults": 250, "timeZone": tzname}
                if page:
                    params["pageToken"] = page
                data = google_get("/calendars/{}/events".format(urllib.parse.quote(cid, safe="")), token, params)
                for e in data.get("items") or []:
                    if not isinstance(e, dict) or e.get("status") == "cancelled":
                        continue
                    me = [a for a in (e.get("attendees") or []) if isinstance(a, dict) and a.get("self")]
                    if me and me[0].get("responseStatus") == "declined":
                        continue
                    s = e.get("start") or {}; f = e.get("end") or {}
                    s_val = s.get("dateTime") or s.get("date")
                    if not isinstance(s_val, str) or not s_val:
                        continue
                    by_date.setdefault(s_val[:10], []).append({
                        "id": e.get("id", ""),
                        "summary": e.get("summary", "(untitled)"),
                        "start": s_val,
                        "end": f.get("dateTime") or f.get("date") or s_val,
                        "location": e.get("location", ""),
                        "description": (e.get("description") or "")[:200],
                        "colorId": e.get("colorId", ""),
                        "recurring": bool(e.get("recurringEventId")),
                        "calendar": cid,
                    })
                page = data.get("nextPageToken")
                if not page:
                    break
        except AgendaError as ex:
            failures.append({"error": "{}: {}".format(cid, ex)})
    if failures:
        by_date.setdefault("unknown", []).extend(failures)
    for day in by_date:
        by_date[day].sort(key=lambda e: e.get("start", ""))
    return document(by_date, "google", cal_ids, now, tz)


def pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def google_login(cfg, timeout=180, open_browser=True):
    """Browser sign-in on a loopback port. Returns the refresh token."""
    g = section(cfg, "google")
    if not g.get("client_id") or not g.get("client_secret"):
        raise AgendaError("the client ID or secret is missing")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 5  # a connection that sends nothing must not stall the wait for the real callback

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404); self.end_headers(); return
            # A callback that does not carry our state was not started by us.
            if q.get("state", [""])[0] != state:
                result["error"] = "unrecognised sign-in response"
                msg = "Sign-in failed."
            elif "error" in q:
                result["error"] = "Google rejected the sign-in: {}".format(q["error"][0])
                msg = "Sign-in failed."
            else:
                result["code"] = q["code"][0]
                msg = "Done. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<!doctype html><meta charset=utf-8><title>Agenda</title><p>{}</p>".format(msg).encode())

        def log_message(self, *a):
            pass

    class QuietServer(http.server.HTTPServer):
        def handle_error(self, request, client_address):
            pass  # a stalled or malformed connection: ignored, the deadline below still holds

    server = QuietServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    redirect = "http://127.0.0.1:{}".format(server.server_address[1])
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": g["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": GOOGLE_SCOPE, "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    print("AUTH_URL " + url, flush=True)
    if open_browser and not os.environ.get("AGENDA_NO_BROWSER"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    deadline = dt.datetime.now() + dt.timedelta(seconds=timeout)
    while not result and dt.datetime.now() < deadline:
        server.handle_request()
    server.server_close()
    if "error" in result:
        raise AgendaError(result["error"])
    if "code" not in result:
        raise AgendaError("timed out waiting for the sign-in")
    tok = http_json(TOKEN_URL, {
        "client_id": g["client_id"], "client_secret": g["client_secret"], "code": result["code"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise AgendaError("Google did not return a session token (revoke the app's access in your Google account and retry)")
    return tok["refresh_token"]


# ------------------------------------------------------------------------- cli

SOURCES = {"ics": fetch_ics_source, "google": fetch_google_source, "command": fetch_command_source}


def sweep_stale(out, older_than=300):
    """Remove the private temp files an earlier fetch left when it was killed (a shell restart or
    the 30 s watchdog stops python before its cleanup runs). A younger file may belong to a fetch
    that is running right now, so only old ones go."""
    folder = os.path.dirname(os.path.abspath(out))
    try:
        names = os.listdir(folder)
    except OSError:
        return
    now = dt.datetime.now().timestamp()
    for name in names:
        if name.startswith(".day-") and (name.endswith(".tmp") or name.startswith(".day-command-")):
            full = os.path.join(folder, name)
            with contextlib.suppress(OSError):
                if now - os.stat(full).st_mtime > older_than:
                    os.unlink(full)


def cmd_fetch(args):
    sweep_stale(args.out)
    cfg = load_config()
    source = cfg.get("source", "")
    if not isinstance(source, str) or source not in SOURCES:
        raise AgendaError("no calendar source is configured")
    doc = SOURCES[source](cfg, args.out)
    write_document(doc, args.out)


def cmd_login(args):
    token = google_login(load_config())
    # The sign-in can take minutes: re-read the config under the lock, so a setting saved
    # meanwhile (by the form or an agent) is kept instead of overwritten by the old copy.
    with config_lock():
        cfg = load_config()
        merge(cfg, {"google": {"refresh_token": token}})
        save_config(cfg)
    print("OK")


def cmd_config_set(args):
    try:
        patch = json.loads(os.environ.get("AGENDA_PATCH", ""))
    except ValueError:
        raise AgendaError("AGENDA_PATCH is not JSON")
    except RecursionError:
        raise AgendaError("AGENDA_PATCH is nested too deeply")
    if not isinstance(patch, dict):
        raise AgendaError("AGENDA_PATCH must be an object")
    if "source" in patch and patch["source"] not in SOURCES:
        raise AgendaError("unknown source: choose ics, google or command")
    for name in ("ics", "google", "command"):
        if name in patch and not isinstance(patch[name], dict):
            raise AgendaError('"{}" must be an object'.format(name))
    with config_lock():
        cfg = load_config()
        apply_patch(cfg, patch)
        save_config(cfg)
    print("OK")


def cmd_config_show(args):
    print(json.dumps(redact(load_config()), ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch"); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_fetch)
    sub.add_parser("google-login").set_defaults(fn=cmd_login)
    sub.add_parser("config-set").set_defaults(fn=cmd_config_set)
    sub.add_parser("config-show").set_defaults(fn=cmd_config_show)
    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except AgendaError as ex:
        print(str(ex), file=sys.stderr)
        return 1
    except Exception as ex:
        # One line and the exception's type, never a traceback: tracebacks quote values, and the
        # values here include credentials.
        print("unexpected failure: {}".format(type(ex).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
