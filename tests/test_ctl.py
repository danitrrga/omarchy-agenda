"""Run: python3 tests/test_ctl.py
A local server stands in for Google, so nothing here touches the network or a real account.
Set AGENDA_CTL to run the same tests against a modified copy (mutation checks)."""
import base64
import hashlib
import http.server
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CTL = os.environ.get("AGENDA_CTL", os.path.join(HERE, "..", "agenda_ctl.py"))
CTL_DIR = os.path.dirname(os.path.abspath(CTL))


class FakeGoogle(http.server.BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path == "/token-html":
            raw = b"<html>maintenance</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(raw)
            return
        body = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        s = FakeGoogle.state
        s.setdefault("token_calls", []).append({k: v[0] for k, v in body.items()})
        g = body.get("grant_type", [""])[0]
        if body.get("client_secret", [""])[0] != "SECRET":
            return self.reply(401, {"error": "invalid_client"})
        if g == "refresh_token":
            if body["refresh_token"][0] != "R1":
                return self.reply(400, {"error": "invalid_grant"})
            return self.reply(200, {"access_token": "ACCESS", "expires_in": 3600})
        if g == "authorization_code":
            verifier = body["code_verifier"][0]
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if body["code"][0] != "CODE1" or digest != s.get("challenge"):
                return self.reply(400, {"error": "invalid_grant"})
            return self.reply(200, {"access_token": "ACCESS", "refresh_token": "R-NEW"})
        self.reply(400, {"error": "unsupported_grant_type"})

    def do_GET(self):
        s = FakeGoogle.state
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if self.headers.get("Authorization") != "Bearer ACCESS":
            return self.reply(401, {"error": {"message": "bad token"}})
        s.setdefault("requests", []).append((url.path, {k: v[0] for k, v in q.items()}))
        if url.path.endswith("/users/me/calendarList"):
            return self.reply(200, {"items": [
                {"id": "me@x.com", "primary": True, "selected": True},
                {"id": "work", "selected": True},
                {"id": "hidden", "selected": False}]})
        if "/calendars/" in url.path and url.path.endswith("/events"):
            cid = urllib.parse.unquote(url.path.split("/calendars/")[1].rsplit("/events", 1)[0])
            if cid == "broken":
                return self.reply(403, {"error": {"message": "forbidden"}})
            if cid == "me@x.com":
                if "pageToken" not in q:
                    return self.reply(200, {"nextPageToken": "P2", "items": [
                        {"id": "e1", "summary": "uno", "start": {"dateTime": "2026-10-09T10:00:00+02:00"}, "end": {"dateTime": "2026-10-09T11:00:00+02:00"}},
                        {"id": "gone", "status": "cancelled", "summary": "cancelado", "start": {"dateTime": "2026-10-09T12:00:00+02:00"}, "end": {"dateTime": "2026-10-09T13:00:00+02:00"}}]})
                return self.reply(200, {"items": [
                    {"id": "e2", "summary": "dos", "recurringEventId": "r", "start": {"dateTime": "2026-10-10T10:00:00+02:00"}, "end": {"dateTime": "2026-10-10T11:00:00+02:00"}},
                    {"id": "no", "summary": "rechazado", "attendees": [{"self": True, "responseStatus": "declined"}], "start": {"dateTime": "2026-10-10T14:00:00+02:00"}, "end": {"dateTime": "2026-10-10T15:00:00+02:00"}},
                    {"id": "ad", "summary": "cumple", "start": {"date": "2026-10-11"}, "end": {"date": "2026-10-12"}}]})
            return self.reply(200, {"items": [
                {"id": "w1", "summary": "trabajo", "start": {"dateTime": "2026-10-09T09:00:00+02:00"}, "end": {"dateTime": "2026-10-09T09:30:00+02:00"}}]})
        self.reply(404, {})


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeGoogle.state = {}
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGoogle)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:{}".format(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.dir.name, "config.json")
        self.out = os.path.join(self.dir.name, "day.json")
        FakeGoogle.state.clear()

    def tearDown(self):
        self.dir.cleanup()

    def env(self, **extra):
        e = dict(os.environ)
        e.update({"AGENDA_CONFIG": self.cfg, "AGENDA_GOOGLE_TOKEN_URL": self.base + "/token",
                  "AGENDA_GOOGLE_API": self.base + "/calendar/v3", "AGENDA_NO_BROWSER": "1"})
        e.update(extra)
        return e

    def ctl(self, *args, **extra):
        return subprocess.run([sys.executable, "-I", CTL, *args], env=self.env(**extra), capture_output=True, text=True, timeout=60)

    def write_cfg(self, obj):
        with open(self.cfg, "w") as f:
            json.dump(obj, f)

    def load_cfg(self):
        with open(self.cfg) as f:
            return json.load(f)

    def raw_cfg(self):
        with open(self.cfg) as f:
            return f.read()

    def day(self):
        with open(self.out) as f:
            return json.load(f)

    def all_events(self):
        return [e for v in self.day()["events_by_date"].values() for e in v]


class CommandSource(Base):
    def printer(self, doc):
        script = os.path.join(self.dir.name, "p.py")
        with open(script, "w") as f:
            f.write("print({!r})\n".format(json.dumps(doc)))
        return "{} {}".format(shlex.quote(sys.executable), shlex.quote(script))

    def test_stdout_mode(self):
        doc = {"events_by_date": {"2026-10-09": [{"id": "a", "summary": "x", "start": "2026-10-09T10:00:00+02:00", "end": "2026-10-09T11:00:00+02:00"}]}}
        self.write_cfg({"source": "command", "command": {"line": self.printer(doc)}})
        r = self.ctl("fetch", "--out", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.all_events()[0]["summary"], "x")

    def test_out_placeholder_mode(self):
        script = os.path.join(self.dir.name, "w.py")
        with open(script, "w") as f:
            f.write("import sys,json\njson.dump({'events_by_date': {}}, open(sys.argv[1],'w'))\n")
        self.write_cfg({"source": "command", "command": {"line": "{} {} {{out}}".format(shlex.quote(sys.executable), shlex.quote(script))}})
        r = self.ctl("fetch", "--out", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.day()["events_by_date"], {})
        self.assertFalse(os.path.exists(self.out + ".command"), "temp file cleaned up")

    def test_failures_are_one_clear_line_and_keep_the_old_file(self):
        with open(self.out, "w") as f:
            f.write('{"events_by_date": {"old": []}}')
        cases = {
            "{} -c 'import sys; sys.exit(3)'".format(shlex.quote(sys.executable)): "code 3",
            "{} -c 'print(1)'".format(shlex.quote(sys.executable)): "events_by_date",
            "{} -c 'print(\"nope\")'".format(shlex.quote(sys.executable)): "valid JSON",
            "/no/such/program": "not found",
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.write_cfg({"source": "command", "command": {"line": line}})
                r = self.ctl("fetch", "--out", self.out)
                self.assertEqual(r.returncode, 1)
                self.assertIn(expected, r.stderr)
                self.assertEqual(r.stderr.strip().count("\n"), 0)
                self.assertIn("old", self.day()["events_by_date"])

    def test_event_without_start_is_rejected(self):
        doc = {"events_by_date": {"d": [{"summary": "x"}]}}
        self.write_cfg({"source": "command", "command": {"line": self.printer(doc)}})
        self.assertEqual(self.ctl("fetch", "--out", self.out).returncode, 1)

    def test_no_source_is_an_error(self):
        r = self.ctl("fetch", "--out", self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no calendar source", r.stderr)


class IcsSource(Base):
    def test_reads_a_feed(self):
        ics = os.path.join(self.dir.name, "c.ics")
        with open(ics, "w") as f:
            f.write("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\nSUMMARY:hoy\r\nDTSTART:20991231T100000Z\r\nDTEND:20991231T110000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        self.write_cfg({"source": "ics", "ics": {"url": ics}})
        r = self.ctl("fetch", "--out", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.day()["source"], "ics")

    def test_a_login_page_is_an_error_not_an_empty_day(self):
        page = os.path.join(self.dir.name, "login.html")
        with open(page, "w") as f:
            f.write("<html>please sign in</html>")
        self.write_cfg({"source": "ics", "ics": {"url": page}})
        r = self.ctl("fetch", "--out", self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not an iCal calendar", r.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_error_never_contains_the_secret_address(self):
        # a space makes http.client raise InvalidURL, whose message quotes the path
        for secret in ("https://127.0.0.1:1/SECRET-TOKEN/basic.ics", "https://127.0.0.1:1/SECRET-TOKEN x/basic.ics"):
            with self.subTest(url=secret):
                self.write_cfg({"source": "ics", "ics": {"url": secret}})
                r = self.ctl("fetch", "--out", self.out)
                self.assertEqual(r.returncode, 1)
                self.assertNotIn("SECRET-TOKEN", r.stderr + r.stdout)


class GoogleSource(Base):
    GOOD = {"source": "google", "google": {"client_id": "CID", "client_secret": "SECRET", "refresh_token": "R1"}}

    def test_fetch_maps_pages_filters_and_calendars(self):
        self.write_cfg(self.GOOD)
        r = self.ctl("fetch", "--out", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        by_summary = {e["summary"]: e for e in self.all_events()}
        self.assertEqual(set(by_summary), {"uno", "dos", "cumple", "trabajo"}, "cancelled and declined dropped, both pages read")
        self.assertTrue(by_summary["dos"]["recurring"])
        self.assertEqual(by_summary["cumple"]["start"], "2026-10-11")
        called = [p for p, _ in FakeGoogle.state["requests"]]
        self.assertFalse(any("hidden" in p for p in called), "calendar switched off in Google is not read")
        ev_params = [q for p, q in FakeGoogle.state["requests"] if p.endswith("/events")][0]
        self.assertEqual(ev_params["singleEvents"], "true")
        self.assertTrue(ev_params["timeMin"].endswith(("+02:00", "+01:00", "Z", "+00:00")) and "T00:00:00" in ev_params["timeMin"], "window starts at local midnight")

    def test_one_failed_calendar_is_flagged_not_hidden(self):
        self.write_cfg({**self.GOOD, "google": {**self.GOOD["google"], "calendars": ["me@x.com", "broken"]}})
        self.assertEqual(self.ctl("fetch", "--out", self.out).returncode, 0)
        errors = [e for e in self.all_events() if "error" in e]
        self.assertEqual(len(errors), 1)
        self.assertIn("broken", errors[0]["error"])

    def test_expired_session_says_how_to_fix_it(self):
        self.write_cfg({**self.GOOD, "google": {**self.GOOD["google"], "refresh_token": "STALE"}})
        r = self.ctl("fetch", "--out", self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("sign in again", r.stderr)
        self.assertIn("7 days", r.stderr)

    def test_bad_client_secret(self):
        self.write_cfg({**self.GOOD, "google": {**self.GOOD["google"], "client_secret": "WRONG"}})
        r = self.ctl("fetch", "--out", self.out)
        self.assertIn("secret", r.stderr)

    def test_credentials_never_reach_output(self):
        self.write_cfg(self.GOOD)
        r = self.ctl("fetch", "--out", self.out)
        with open(self.out) as f:
            blob = f.read() + r.stdout + r.stderr
        for secret in ("SECRET", "R1", "ACCESS", "CID"):
            self.assertNotIn(secret, blob)


class GoogleLogin(Base):
    def login(self, tamper_state=False, wrong_code=False):
        self.write_cfg({"source": "google", "google": {"client_id": "CID", "client_secret": "SECRET"}})
        p = subprocess.Popen([sys.executable, "-I", CTL, "google-login"], env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = p.stdout.readline().strip()
        self.assertTrue(line.startswith("AUTH_URL "), line)
        url = urllib.parse.urlparse(line.split(" ", 1)[1])
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        FakeGoogle.state["challenge"] = q["code_challenge"]
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual(q["scope"], "https://www.googleapis.com/auth/calendar.readonly", "read-only scope")
        self.assertEqual(q["access_type"], "offline")
        state = "forged" if tamper_state else q["state"]
        code = "WRONG" if wrong_code else "CODE1"
        try:
            urllib.request.urlopen("{}?code={}&state={}".format(q["redirect_uri"], code, state), timeout=10).read()
        except Exception:
            pass
        out, err = p.communicate(timeout=30)
        return p.returncode, out, err

    def test_happy_path_stores_the_refresh_token_privately(self):
        rc, out, err = self.login()
        self.assertEqual(rc, 0, err)
        with open(self.cfg) as f:
            self.assertEqual(json.load(f)["google"]["refresh_token"], "R-NEW")
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg).st_mode), 0o600)
        self.assertNotIn("R-NEW", out + err)

    def test_forged_state_is_refused(self):
        rc, out, err = self.login(tamper_state=True)
        self.assertEqual(rc, 1)
        with open(self.cfg) as f:
            self.assertNotIn("refresh_token", json.load(f).get("google", {}))

    def start_login(self):
        self.write_cfg({"source": "google", "google": {"client_id": "CID", "client_secret": "SECRET"}})
        p = subprocess.Popen([sys.executable, "-I", CTL, "google-login"], env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = p.stdout.readline().strip()
        self.assertTrue(line.startswith("AUTH_URL "), line)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(line.split(" ", 1)[1]).query).items()}
        FakeGoogle.state["challenge"] = q["code_challenge"]
        return p, q

    def test_a_setting_saved_during_the_sign_in_survives_it(self):
        p, q = self.start_login()
        r = self.ctl("config-set", AGENDA_PATCH=json.dumps({"command": {"line": "echo saved-meanwhile"}}))
        self.assertEqual(r.returncode, 0, r.stderr)
        urllib.request.urlopen("{}?code=CODE1&state={}".format(q["redirect_uri"], q["state"]), timeout=10).read()
        self.assertEqual(p.wait(timeout=30), 0)
        cfg = self.load_cfg()
        self.assertEqual(cfg["google"]["refresh_token"], "R-NEW")
        self.assertEqual(cfg["command"]["line"], "echo saved-meanwhile", "the sign-in wrote back the config it read at the start")

    def test_a_silent_connection_cannot_hold_the_sign_in_hostage(self):
        import socket
        p, q = self.start_login()
        port = int(urllib.parse.urlparse(q["redirect_uri"]).port)
        stalled = socket.create_connection(("127.0.0.1", port))  # connects, sends nothing
        try:
            urllib.request.urlopen("{}?code=CODE1&state={}".format(q["redirect_uri"], q["state"]), timeout=20).read()
            self.assertEqual(p.wait(timeout=30), 0)
        finally:
            stalled.close()
            if p.poll() is None:
                p.kill()

    def test_wrong_code_fails_cleanly(self):
        rc, out, err = self.login(wrong_code=True)
        self.assertEqual(rc, 1)
        self.assertEqual(err.strip().count("\n"), 0)


class Config(Base):
    def test_set_merges_prunes_blanks_and_stays_private(self):
        self.write_cfg({"source": "google", "google": {"client_id": "A", "client_secret": "B", "refresh_token": "R"}})
        r = subprocess.run([sys.executable, "-I", CTL, "config-set"], env=self.env(AGENDA_PATCH=json.dumps(
            {"source": "ics", "ics": {"url": "https://x/y.ics"}, "google": {"client_id": "", "client_secret": ""}})), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.cfg) as f:
            cfg = json.load(f)
        self.assertEqual(cfg["source"], "ics")
        self.assertEqual(cfg["google"], {"client_id": "A", "client_secret": "B", "refresh_token": "R"}, "blank field keeps the stored credential")
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg).st_mode), 0o600)

    def test_show_redacts_every_secret(self):
        self.write_cfg({"source": "google", "ics": {"url": "https://secret"}, "google": {"client_id": "A", "client_secret": "B", "refresh_token": "R"}, "command": {"line": "x"}})
        r = self.ctl("config-show")
        for secret in ("https://secret", '"A"', '"B"', '"R"'):
            self.assertNotIn(secret, r.stdout)
        shown = json.loads(r.stdout)
        self.assertEqual(shown["google"]["refresh_token"], "set")
        self.assertEqual(shown["source"], "google")


class Hardening(Base):
    """Each case is a failure the 2026-10-08 adversarial pass reproduced. A tampered, half-written or
    hostile input must end in one line and exit 1, never a traceback (it would quote values), and
    must never lose or corrupt the user's settings."""

    def one_line_failure(self, r, what):
        self.assertEqual(r.returncode, 1, "%s: %r" % (what, r.stderr))
        self.assertNotIn("Traceback", r.stderr, what)
        self.assertEqual(r.stderr.strip().count("\n"), 0, "%s: %r" % (what, r.stderr))

    def test_config_of_the_wrong_shape_is_one_line(self):
        for cfg in ({"source": ["ics"]}, {"source": "ics", "ics": "https://x"}, {"source": "command", "command": "x"},
                    {"source": "google", "google": "x"}):
            self.write_cfg(cfg)
            self.one_line_failure(self.ctl("fetch", "--out", self.out), json.dumps(cfg))

    def test_config_that_is_a_directory_or_unreadable(self):
        os.mkdir(self.cfg)
        self.one_line_failure(self.ctl("fetch", "--out", self.out), "directory")
        self.one_line_failure(self.ctl("config-show"), "directory, config-show")
        os.rmdir(self.cfg)
        self.write_cfg({"source": "ics"})
        os.chmod(self.cfg, 0)
        try:
            self.one_line_failure(self.ctl("fetch", "--out", self.out), "chmod 000")
        finally:
            os.chmod(self.cfg, 0o600)

    def test_output_that_cannot_be_written_is_one_line(self):
        self.raw_command("print('{\"events_by_date\": {}}')\n")
        os.mkdir(self.out)  # --out is a directory
        self.one_line_failure(self.ctl("fetch", "--out", self.out), "--out is a directory")

    def raw_command(self, code):
        script = os.path.join(self.dir.name, "raw.py")
        with open(script, "w") as f:
            f.write(code)
        self.write_cfg({"source": "command", "command": {"line": "{} {}".format(shlex.quote(sys.executable), shlex.quote(script))}})

    def test_command_output_that_is_not_clean_json(self):
        cases = (
            ("invalid utf-8", "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe')\n"),
            ("deeply nested", "print('[' * 100000)\n"),
            ("NaN", "print('{\"events_by_date\": {}, \"x\": NaN}')\n"),
            ("lone surrogate", "print('{\"events_by_date\": {\"2026-10-09\": [{\"start\": \"2026-10-09T10:00:00\", \"summary\": \"\\\\ud800\"}]}}')\n"),
        )
        for name, code in cases:
            self.raw_command(code)
            r = self.ctl("fetch", "--out", self.out)
            self.one_line_failure(r, name)
            self.assertFalse(os.path.exists(self.out), name + ": a failed fetch leaves no day.json")
            self.assertEqual([f for f in os.listdir(self.dir.name) if f.startswith(".day-")], [], name + ": no temp file left")

    def test_non_event_entries_from_a_command_are_dropped(self):
        self.raw_command("print('{\"events_by_date\": {\"2026-10-09\": [\"junk\", 5, {\"start\": \"2026-10-09T10:00:00\", \"summary\": \"ok\"}]}}')\n")
        self.assertEqual(self.ctl("fetch", "--out", self.out).returncode, 0)
        self.assertEqual([e["summary"] for e in self.all_events()], ["ok"])

    def test_a_stale_command_file_is_never_reused(self):
        with open(self.out + ".command", "w") as f:
            json.dump({"events_by_date": {"STALE": []}}, f)
        self.write_cfg({"source": "command", "command": {"line": "{} -c pass {{out}}".format(shlex.quote(sys.executable))}})
        r = self.ctl("fetch", "--out", self.out)
        self.one_line_failure(r, "a command that wrote nothing")
        self.assertIn("valid JSON", r.stderr)

    def test_a_command_that_fails_quietly_has_its_cause_in_the_error(self):
        # Control, seen failing before the change in agenda_ctl.py: with a command that exits 0, writes
        # nothing and says why on stderr, lastError was only "the command did not produce valid JSON"
        # (AssertionError: 'deleted_client: The OAuth client was deleted' not found in 'the command did not produce valid JSON\n').
        # The cause is the command's last stderr line, cut to 200 characters, so it stays one line.
        self.raw_command("import sys\nsys.stderr.write('[calendar-cache] first\\n[calendar-cache] refresh failed: deleted_client: The OAuth client was deleted.\\n')\n")
        r = self.ctl("fetch", "--out", self.out)
        self.one_line_failure(r, "a command that exits 0 and prints its cause on stderr")
        self.assertIn("valid JSON", r.stderr)
        self.assertIn("deleted_client: The OAuth client was deleted", r.stderr)
        self.assertNotIn("first", r.stderr, "only the last stderr line")
        self.raw_command("import sys\nsys.stderr.write('x' * 5000 + '\\n')\n")
        long = self.ctl("fetch", "--out", self.out)
        self.one_line_failure(long, "a long stderr line")
        self.assertLess(len(long.stderr), 300)

    def test_parallel_fetches_do_not_trample_each_other(self):
        self.raw_command("import json\nprint(json.dumps({'events_by_date': {'2026-10-09': [{'start': '2026-10-09T10:00:00', 'summary': 'x' * 100} for _ in range(30000)]}}))\n")
        procs = [subprocess.Popen([sys.executable, "-I", CTL, "fetch", "--out", self.out], env=self.env(),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
        results = [(p.wait(timeout=120), p.stderr.read()) for p in procs]
        self.assertEqual([c for c, _ in results], [0] * 6, results)
        self.assertEqual(len(self.all_events()), 30000)

    def test_temp_files_of_a_killed_fetch_are_swept_but_a_running_one_is_not(self):
        old, fresh = self.out.replace("day.json", ".day-command-old"), self.out.replace("day.json", ".day-new.tmp")
        for path in (old, fresh):
            with open(path, "w") as f:
                f.write("x")
        os.utime(old, (1, 1))  # a file from 1970: its fetch is long dead
        self.raw_command("print('{\"events_by_date\": {}}')\n")
        self.assertEqual(self.ctl("fetch", "--out", self.out).returncode, 0)
        self.assertFalse(os.path.exists(old), "an old temp file is removed")
        self.assertTrue(os.path.exists(fresh), "a young one may belong to a fetch that is running now")

    def test_day_json_is_private(self):
        self.raw_command("print('{\"events_by_date\": {}}')\n")
        self.assertEqual(self.ctl("fetch", "--out", self.out).returncode, 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o600)

    def patch(self, obj):
        return self.ctl("config-set", AGENDA_PATCH=obj if isinstance(obj, str) else json.dumps(obj))

    def test_config_set_refuses_what_would_break_every_later_fetch(self):
        self.write_cfg({"source": "ics", "ics": {"url": "https://x/y.ics"}})
        before = self.raw_cfg()
        for bad in ({"source": ["ics"]}, {"source": "ftp"}, {"ics": "https://x"}, {"google": ["a"]}, "[" * 100000,
                    {"ics": {"url": "\ud800"}}):
            self.one_line_failure(self.patch(bad), repr(bad)[:40])
        self.assertEqual(self.raw_cfg(), before, "a refused patch changes nothing")

    def test_emptying_a_field_clears_it_but_never_a_credential(self):
        self.write_cfg({"source": "command", "command": {"line": "x", "watch": "/some/file"},
                        "google": {"client_id": "A", "client_secret": "B", "refresh_token": "R"}})
        self.assertEqual(self.patch({"command": {"watch": ""}, "google": {"client_secret": ""}}).returncode, 0)
        cfg = self.load_cfg()
        self.assertNotIn("watch", cfg["command"])
        self.assertEqual(cfg["google"]["client_secret"], "B")

    def test_a_new_google_client_drops_the_old_session(self):
        self.write_cfg({"source": "google", "google": {"client_id": "A", "client_secret": "B", "refresh_token": "R"}})
        self.assertEqual(self.patch({"google": {"client_id": "A", "client_secret": "B"}}).returncode, 0)
        self.assertEqual(self.load_cfg()["google"].get("refresh_token"), "R", "same client: session kept")
        self.assertEqual(self.patch({"google": {"client_id": "A2"}}).returncode, 0)
        self.assertNotIn("refresh_token", self.load_cfg()["google"], "another client: the old token is not its own")

    def test_concurrent_config_set_loses_nothing(self):
        self.write_cfg({"source": "ics", "ics": {"url": "https://x/y.ics"}})
        procs = [subprocess.Popen([sys.executable, "-I", CTL, "config-set"], env=self.env(AGENDA_PATCH=json.dumps({"extra": {"k%d" % i: i}})),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(60)]
        self.assertEqual([p.wait(timeout=60) for p in procs], [0] * 60)
        self.assertEqual(sorted(self.load_cfg()["extra"]), sorted("k%d" % i for i in range(60)))

    def test_show_hides_a_command_s_arguments(self):
        self.write_cfg({"source": "command", "command": {"line": "curl -H 'X-Token: FAKE-SECRET-123' https://h/x"}})
        r = self.ctl("config-show")
        self.assertNotIn("FAKE-SECRET-123", r.stdout)
        self.assertIn("curl", r.stdout)

    def test_google_answers_that_are_not_json_are_one_line(self):
        self.write_cfg({"source": "google", "google": {"client_id": "CID", "client_secret": "SECRET", "refresh_token": "R1"}})
        self.one_line_failure(self.ctl("fetch", "--out", self.out, AGENDA_GOOGLE_TOKEN_URL=self.base + "/token-html"), "html token reply")

    def test_calendars_given_as_one_string_is_one_calendar(self):
        self.write_cfg({"source": "google", "google": {"client_id": "CID", "client_secret": "SECRET", "refresh_token": "R1",
                                                        "calendars": "me@x.com"}})
        self.assertEqual(self.ctl("fetch", "--out", self.out).returncode, 0)
        self.assertEqual(self.day()["calendars"], ["me@x.com"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
