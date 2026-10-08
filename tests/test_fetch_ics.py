"""Run: python3 tests/test_fetch_ics.py   (the dateutil cross-check needs a python with dateutil)"""
import datetime as dt
import importlib.util
import os
import unittest
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("fetch_ics", os.environ.get("FETCH_ICS", os.path.join(HERE, "..", "fetch_ics.py")))
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)
AMS = ZoneInfo("Europe/Amsterdam")
NOW = dt.datetime(2026, 10, 8, 16, 6, tzinfo=AMS)


def cal(*events):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join("BEGIN:VEVENT\r\n" + e.strip() + "\r\nEND:VEVENT\r\n" for e in events) + "END:VCALENDAR\r\n"


def run(*events, days=7, now=NOW):
    payload, unsupported = F.build(cal(*events), now, days, AMS)
    flat = [e for k in sorted(payload["events_by_date"]) for e in payload["events_by_date"][k]]
    return flat, unsupported


def titles(flat):
    return [(e["summary"], e["start"][:16]) for e in flat]


class Parsing(unittest.TestCase):
    def test_tzid_and_utc_land_on_the_same_wall_clock(self):
        flat, _ = run("UID:a\nSUMMARY:tz\nDTSTART;TZID=Europe/Amsterdam:20261009T050000\nDTEND;TZID=Europe/Amsterdam:20261009T051500",
                      "UID:b\nSUMMARY:utc\nDTSTART:20261009T030000Z\nDTEND:20261009T031500Z")
        self.assertEqual(titles(flat), [("tz", "2026-10-09T05:00"), ("utc", "2026-10-09T05:00")])

    def test_all_day_is_a_date_not_2am(self):
        flat, _ = run("UID:d\nSUMMARY:cumple\nDTSTART;VALUE=DATE:20261009\nDTEND;VALUE=DATE:20261010")
        self.assertEqual((flat[0]["start"], flat[0]["end"]), ("2026-10-09", "2026-10-10"))

    def test_unfolding_and_escapes(self):
        flat, _ = run("UID:e\nSUMMARY:uno\\, dos\nDESCRIPTION:linea1\\nlin\r\n ea2\nDTSTART:20261009T100000Z\nDTEND:20261009T110000Z")
        self.assertEqual(flat[0]["summary"], "uno, dos")
        self.assertEqual(flat[0]["description"], "linea1\nlinea2")

    def test_cancelled_and_out_of_window_are_dropped(self):
        flat, _ = run("UID:c\nSUMMARY:x\nSTATUS:CANCELLED\nDTSTART:20261009T100000Z\nDTEND:20261009T110000Z",
                      "UID:o\nSUMMARY:far\nDTSTART:20261201T100000Z\nDTEND:20261201T110000Z",
                      "UID:p\nSUMMARY:past\nDTSTART:20261001T100000Z\nDTEND:20261001T110000Z")
        self.assertEqual(flat, [])

    def test_block_that_ended_this_morning_is_kept(self):
        # the widget needs the day from midnight, not from "now"
        flat, _ = run("UID:m\nSUMMARY:morning\nDTSTART:20261008T060000Z\nDTEND:20261008T070000Z")
        self.assertEqual(len(flat), 1)

    def test_overnight_block_from_yesterday_is_kept(self):
        flat, _ = run("UID:n\nSUMMARY:party\nDTSTART:20261007T190000Z\nDTEND:20261007T230000Z")
        self.assertEqual(titles(flat), [("party", "2026-10-07T21:00")])

    def test_duration_instead_of_dtend(self):
        flat, _ = run("UID:du\nSUMMARY:d\nDTSTART:20261009T100000Z\nDURATION:PT1H30M")
        self.assertEqual(flat[0]["end"][11:16], "13:30")


class Recurrence(unittest.TestCase):
    def test_weekly_byday_exdate_and_override(self):
        master = ("UID:w\nSUMMARY:clase\nDTSTART;TZID=Europe/Amsterdam:20261005T100000\nDTEND;TZID=Europe/Amsterdam:20261005T110000\n"
                  "RRULE:FREQ=WEEKLY;BYDAY=MO,WE\nEXDATE;TZID=Europe/Amsterdam:20261012T100000")
        moved = ("UID:w\nSUMMARY:clase movida\nRECURRENCE-ID;TZID=Europe/Amsterdam:20261014T100000\n"
                 "DTSTART;TZID=Europe/Amsterdam:20261014T150000\nDTEND;TZID=Europe/Amsterdam:20261014T160000")
        flat, _ = run(master, moved, days=7)
        got = [t for t in titles(flat)]
        self.assertNotIn(("clase", "2026-10-12T10:00"), got, "EXDATE removed")
        self.assertNotIn(("clase", "2026-10-14T10:00"), got, "overridden instance removed")
        self.assertIn(("clase movida", "2026-10-14T15:00"), got)

    def test_cancelled_instance_of_a_series(self):
        master = "UID:s\nSUMMARY:s\nDTSTART:20261009T080000Z\nDTEND:20261009T090000Z\nRRULE:FREQ=DAILY"
        gone = "UID:s\nSUMMARY:s\nSTATUS:CANCELLED\nRECURRENCE-ID:20261010T080000Z\nDTSTART:20261010T080000Z\nDTEND:20261010T090000Z"
        flat, _ = run(master, gone, days=7)
        self.assertNotIn("2026-10-10", [e["start"][:10] for e in flat])
        self.assertIn("2026-10-11", [e["start"][:10] for e in flat])

    def test_wall_clock_survives_dst_change(self):
        # Europe/Amsterdam leaves summer time on 2026-10-25: 09:00 stays 09:00
        flat, _ = run("UID:d\nSUMMARY:x\nDTSTART;TZID=Europe/Amsterdam:20261019T090000\nDTEND;TZID=Europe/Amsterdam:20261019T100000\nRRULE:FREQ=DAILY",
                      days=10, now=dt.datetime(2026, 10, 22, 12, 0, tzinfo=AMS))
        self.assertEqual({e["start"][11:16] for e in flat}, {"09:00"})
        self.assertEqual({e["start"][19:] for e in flat}, {"+02:00", "+01:00"})

    def test_unsupported_rule_shows_once_and_is_reported(self):
        flat, unsupported = run("UID:u\nSUMMARY:u\nDTSTART:20261009T080000Z\nDTEND:20261009T090000Z\nRRULE:FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=-1")
        self.assertEqual((len(flat), unsupported), (1, 1))

    def test_count_counts_from_the_first_date(self):
        # 3 occurrences starting 2026-10-05: 5th, 6th, 7th. none in the window from the 8th.
        flat, _ = run("UID:c\nSUMMARY:c\nDTSTART:20261005T080000Z\nDTEND:20261005T090000Z\nRRULE:FREQ=DAILY;COUNT=3")
        self.assertEqual(flat, [])

    def test_until_is_inclusive(self):
        flat, _ = run("UID:t\nSUMMARY:t\nDTSTART:20261005T080000Z\nDTEND:20261005T090000Z\nRRULE:FREQ=DAILY;UNTIL=20261010T080000Z")
        self.assertEqual([e["start"][:10] for e in flat], ["2026-10-08", "2026-10-09", "2026-10-10"])


class AgainstDateutil(unittest.TestCase):
    """The rule expander against dateutil, an independent implementation."""
    RULES = [
        "FREQ=DAILY", "FREQ=DAILY;INTERVAL=3", "FREQ=WEEKLY", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH",
        "FREQ=WEEKLY;BYDAY=MO,WE,FR;WKST=SU", "FREQ=MONTHLY", "FREQ=MONTHLY;BYMONTHDAY=1,15,-1",
        "FREQ=MONTHLY;BYDAY=1MO", "FREQ=MONTHLY;BYDAY=-1FR", "FREQ=MONTHLY;INTERVAL=2;BYDAY=2WE",
        "FREQ=YEARLY", "FREQ=YEARLY;BYMONTH=10;BYDAY=2TH", "FREQ=DAILY;COUNT=40", "FREQ=WEEKLY;COUNT=9;BYDAY=SA",
        "FREQ=DAILY;UNTIL=20261115T000000Z", "FREQ=MONTHLY;BYMONTHDAY=31",
    ]

    def test_matches_dateutil(self):
        try:
            from dateutil.rrule import rrulestr
        except ImportError:
            self.skipTest("dateutil not installed")
        start = dt.datetime(2026, 9, 2, 9, 30, tzinfo=AMS)
        ev_template = "UID:r\nSUMMARY:r\nDTSTART;TZID=Europe/Amsterdam:20260902T093000\nDTEND;TZID=Europe/Amsterdam:20260902T103000\nRRULE:{}"
        now = dt.datetime(2026, 10, 1, 12, 0, tzinfo=AMS)
        for rule in self.RULES:
            with self.subTest(rule=rule):
                payload, _ = F.build(cal(ev_template.format(rule)), now, 62, AMS)
                got = sorted(e["start"][:16] for v in payload["events_by_date"].values() for e in v)
                lo = now.replace(hour=0, minute=0)
                hi = lo + dt.timedelta(days=62)
                want = sorted(o.astimezone(AMS).strftime("%Y-%m-%dT%H:%M")
                              for o in rrulestr(rule, dtstart=start) if lo <= o < hi
                              for o in [o])
                self.assertEqual(got, want)


class Hostile(unittest.TestCase):
    """The 2026-10-08 adversarial pass: one malformed event must cost that event, not the calendar."""
    GOOD = "UID:good\nSUMMARY:good\nDTSTART:20261009T120000Z\nDTEND:20261009T130000Z"

    def test_a_bad_event_is_skipped_and_the_rest_survives(self):
        for bad in ("DTSTART:2026-10-09T10:00:00Z", "DTSTART:20261340T100000Z",
                    "DTSTART:20261009T100000Z\nRRULE:FREQ=DAILY;INTERVAL=abc",
                    "DTSTART:20261009T100000Z\nRRULE:FREQ=DAILY;COUNT=abc",
                    "DTSTART:20261009T100000Z\nRRULE:FREQ=DAILY;UNTIL=soon",
                    "DTSTART:20261009T100000Z\nEXDATE:notadate", "DTSTART:20261009T100000Z\nRDATE:garbage",
                    "DTSTART:99991231T230000Z\nDURATION:PT2H",
                    "DTSTART:20261009T100000Z\nDTEND:99991231T000000Z\nRRULE:FREQ=DAILY"):
            payload, _ = F.build(cal(self.GOOD, "UID:bad\nSUMMARY:bad\n" + bad), NOW, 7, AMS)
            flat = [e["summary"] for v in payload["events_by_date"].values() for e in v]
            self.assertEqual(flat, ["good"], bad)
            self.assertEqual(payload["skipped"], 1, bad)

    def test_a_fractional_or_year_duration_is_skipped_not_misread(self):
        flat, _ = run("UID:a\nSUMMARY:frac\nDTSTART:20261009T100000Z\nDURATION:PT1.5H",
                      "UID:b\nSUMMARY:year\nDTSTART:20261009T100000Z\nDURATION:P1Y",
                      "UID:c\nSUMMARY:ok\nDTSTART:20261009T100000Z\nDURATION:P1DT2H30M")
        self.assertEqual([(e["summary"], e["end"][:16]) for e in flat], [("ok", "2026-10-10T14:30")])

    def test_an_end_before_the_start_is_a_zero_length_block(self):
        flat, _ = run("UID:r\nSUMMARY:rev\nDTSTART:20261009T100000Z\nDTEND:20261009T090000Z")
        self.assertEqual(flat[0]["start"], flat[0]["end"])

    def test_a_fifo_is_refused_not_waited_on(self):
        import tempfile
        d = tempfile.mkdtemp()
        fifo = os.path.join(d, "f.ics")
        os.mkfifo(fifo)
        try:
            with self.assertRaises(ValueError):
                F.read_source(fifo)
        finally:
            os.unlink(fifo)
            os.rmdir(d)


if __name__ == "__main__":
    unittest.main(verbosity=1)
