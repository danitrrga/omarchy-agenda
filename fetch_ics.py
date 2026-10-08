#!/usr/bin/env python3
"""iCal reader for the agenda's `ics` source (called by agenda_ctl.py).

Standard library only, so it runs under the system python3 with nothing
installed. The feed is a Google Calendar "secret address in iCal format" or any
other .ics URL (Proton, Fastmail, Nextcloud, ...), http(s):// or webcal://.

The URL is a credential: whoever has it can read the calendar. Callers must
never echo it, and this module never prints.

build() returns the same document every source produces, so the widget reads
all of them the same way:
  { as_of, window_days, source, calendars, events_by_date: { "YYYY-MM-DD": [
      { id, summary, start, end, location, description, colorId, recurring, calendar } ] } }

Recurrence rules are expanded here, because a feed carries the rule and not the
occurrences. Supported: FREQ=DAILY|WEEKLY|MONTHLY|YEARLY with INTERVAL, COUNT,
UNTIL, BYDAY (with a monthly ordinal such as 1MO or -1FR), BYMONTHDAY, BYMONTH,
EXDATE, RDATE and RECURRENCE-ID overrides, wall-clock times kept across DST.
A rule outside that set (BYSETPOS, BYWEEKNO, BYYEARDAY, HOURLY and finer) is not
guessed at: the event shows once, at its first date, and the count of such
events is reported on stderr.
"""
import datetime as dt
import os
import re
import urllib.request
from zoneinfo import ZoneInfo

MAX_BYTES = 25 * 1024 * 1024
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
UNSUPPORTED_PARTS = ("BYSETPOS", "BYWEEKNO", "BYYEARDAY", "BYHOUR", "BYMINUTE", "BYSECOND")


def local_zone():
    name = os.environ.get("TZ", "")
    try:
        if name:
            return ZoneInfo(name)
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return ZoneInfo(target.split("zoneinfo/", 1)[1])
    except Exception:
        pass
    return dt.datetime.now().astimezone().tzinfo


# --------------------------------------------------------------------- parsing

def unfold(text):
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def split_line(line):
    """'NAME;P=V;P2="a:b":value' -> (NAME, {P: V}, value)."""
    in_quote = False
    cut = -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            cut = i
            break
    if cut < 0:
        return None
    head, value = line[:cut], line[cut + 1:]
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def unescape(text):
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def components(text):
    """Yield each VEVENT as a list of (name, params, value)."""
    current = None
    for line in unfold(text):
        if line.upper() == "BEGIN:VEVENT":
            current = []
        elif line.upper() == "END:VEVENT":
            if current is not None:
                yield current
            current = None
        elif current is not None:
            parsed = split_line(line)
            if parsed:
                current.append(parsed)


def zone_for(params, default):
    tzid = params.get("TZID")
    if not tzid:
        return default
    try:
        return ZoneInfo(tzid)
    except Exception:
        return default  # an unknown (e.g. Windows) zone name: read as local time


def parse_when(value, params, default_tz):
    """-> (datetime or date, is_date). A date means an all-day value."""
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or (len(v) == 8 and "T" not in v):
        return dt.date(int(v[0:4]), int(v[4:6]), int(v[6:8])), True
    utc = v.endswith("Z")
    v = v.rstrip("Zz")
    naive = dt.datetime(int(v[0:4]), int(v[4:6]), int(v[6:8]), int(v[9:11]), int(v[11:13]), int(v[13:15] or 0))
    if utc:
        return naive.replace(tzinfo=dt.timezone.utc), False
    return naive.replace(tzinfo=zone_for(params, default_tz)), False


_DURATION = re.compile(r"^([+-]?)P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration(value):
    """RFC 5545 durations in whole units (P1W, P1DT2H, PT45M). Anything else, a fraction or a
    year, raises ValueError and the event is skipped: a wrong length is worse than none."""
    m = _DURATION.match(value.strip().upper())
    if not m:
        raise ValueError("unsupported DURATION")
    sign = -1 if m.group(1) == "-" else 1
    weeks, days, hours, minutes, seconds = (int(g or 0) for g in m.groups()[1:])
    return sign * dt.timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_event(props, default_tz):
    """One VEVENT -> a plain dict, or None when it has no usable start."""
    ev = {"exdates": [], "rdates": [], "rule": None, "status": "", "recurrence_id": None}
    duration = None
    for name, params, value in props:
        if name == "UID": ev["uid"] = value
        elif name == "SUMMARY": ev["summary"] = unescape(value)
        elif name == "LOCATION": ev["location"] = unescape(value)
        elif name == "DESCRIPTION": ev["description"] = unescape(value)
        elif name == "STATUS": ev["status"] = value.upper()
        elif name == "DTSTART": ev["start"], ev["all_day"] = parse_when(value, params, default_tz)
        elif name == "DTEND": ev["end"], _ = parse_when(value, params, default_tz)
        elif name == "DURATION": duration = parse_duration(value)
        elif name == "RRULE":
            ev["rule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
        elif name == "EXDATE":
            for part in value.split(","):
                ev["exdates"].append(parse_when(part, params, default_tz)[0])
        elif name == "RDATE":
            for part in value.split(","):
                if "/" not in part:
                    ev["rdates"].append(parse_when(part, params, default_tz)[0])
        elif name == "RECURRENCE-ID":
            ev["recurrence_id"] = parse_when(value, params, default_tz)[0]
    if "start" not in ev:
        return None
    if "end" not in ev:
        if duration is not None:
            ev["end"] = ev["start"] + duration
        elif ev["all_day"]:
            ev["end"] = ev["start"] + dt.timedelta(days=1)
        else:
            ev["end"] = ev["start"]
    if ev["end"] < ev["start"]:  # a reversed event is a zero-length one, not a negative one
        ev["end"] = ev["start"]
    ev.setdefault("uid", "")
    ev.setdefault("summary", "(untitled)")
    ev.setdefault("location", "")
    ev.setdefault("description", "")
    return ev


# ------------------------------------------------------------------ recurrence

def instant(value, tz):
    """A comparable moment for a datetime or a date (all-day: local midnight)."""
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime(value.year, value.month, value.day, tzinfo=tz)


def parse_byday(text):
    out = []
    for item in text.split(","):
        item = item.strip().upper()
        wd = item[-2:]
        if wd not in WEEKDAYS:
            continue
        ordinal = int(item[:-2]) if item[:-2] else 0
        out.append((ordinal, WEEKDAYS.index(wd)))
    return out


def nth_weekday_matches(day, ordinal, weekday):
    if day.weekday() != weekday:
        return False
    if ordinal == 0:
        return True
    if ordinal > 0:
        return (day.day - 1) // 7 + 1 == ordinal
    # counted from the end of the month
    next_month = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    last_day = (next_month - dt.timedelta(days=1)).day
    return -((last_day - day.day) // 7 + 1) == ordinal


def rule_supported(rule):
    if rule.get("FREQ") not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return False
    return not any(part in rule for part in UNSUPPORTED_PARTS)


def day_matches(rule, d0, day):
    """Does the rule produce an occurrence on `day`, given it starts on d0?"""
    if day < d0:
        return False
    freq = rule["FREQ"]
    interval = max(1, int(rule.get("INTERVAL", 1)))
    byday = parse_byday(rule["BYDAY"]) if "BYDAY" in rule else []
    bymonthday = [int(x) for x in rule["BYMONTHDAY"].split(",")] if "BYMONTHDAY" in rule else []
    bymonth = [int(x) for x in rule["BYMONTH"].split(",")] if "BYMONTH" in rule else []
    wkst = WEEKDAYS.index(rule.get("WKST", "MO")) if rule.get("WKST", "MO") in WEEKDAYS else 0

    def month_day_ok():
        if not bymonthday:
            return True
        last = ((day.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)).day
        return day.day in bymonthday or (day.day - last - 1) in bymonthday

    if bymonth and day.month not in bymonth:
        return False
    if freq == "DAILY":
        if (day - d0).days % interval:
            return False
        return (not byday or any(nth_weekday_matches(day, 0, wd) for _, wd in byday)) and month_day_ok()
    if freq == "WEEKLY":
        def week_start(d):
            return d - dt.timedelta(days=(d.weekday() - wkst) % 7)
        if ((week_start(day) - week_start(d0)).days // 7) % interval:
            return False
        wanted = {wd for _, wd in byday} or {d0.weekday()}
        return day.weekday() in wanted
    if freq == "MONTHLY":
        if ((day.year - d0.year) * 12 + day.month - d0.month) % interval:
            return False
        if bymonthday:
            return month_day_ok()
        if byday:
            return any(nth_weekday_matches(day, o, wd) for o, wd in byday)
        return day.day == d0.day
    # YEARLY
    if (day.year - d0.year) % interval:
        return False
    if not bymonth and day.month != d0.month:
        return False
    if bymonthday:
        return month_day_ok()
    if byday:
        return any(nth_weekday_matches(day, o, wd) for o, wd in byday)
    return day.day == d0.day


def occurrences(ev, window_start, window_end, tz):
    """Start moments of every occurrence of `ev` that could touch the window."""
    start = ev["start"]
    all_day = ev["all_day"]
    ev_tz = start.tzinfo if isinstance(start, dt.datetime) and start.tzinfo else tz
    rule = ev["rule"]
    if not rule:
        return [start]
    if not rule_supported(rule):
        return [start]
    d0 = (start.astimezone(ev_tz).date() if isinstance(start, dt.datetime) else start)
    clock = start.astimezone(ev_tz).timetz().replace(tzinfo=None) if isinstance(start, dt.datetime) else None
    span = max(ev["end"] - start, dt.timedelta(0))
    until = None
    if "UNTIL" in rule:
        until_val, _ = parse_when(rule["UNTIL"], {}, ev_tz)
        until = instant(until_val, ev_tz)
    count = int(rule["COUNT"]) if "COUNT" in rule else None

    lo = (window_start - span - dt.timedelta(days=1)).astimezone(ev_tz).date()
    hi = (window_end + dt.timedelta(days=1)).astimezone(ev_tz).date()
    # COUNT depends on everything before the window, so it must be counted from
    # the first date; without it only the window days need visiting.
    cursor = d0 if count is not None else max(d0, lo)
    exdates = {instant(x, ev_tz) for x in ev["exdates"]}
    out = []
    produced = 0
    safety = 0
    while cursor <= hi and safety < 40000:
        safety += 1
        if day_matches(rule, d0, cursor):
            if all_day:
                moment = dt.datetime(cursor.year, cursor.month, cursor.day, tzinfo=ev_tz)
            else:
                moment = dt.datetime.combine(cursor, clock).replace(tzinfo=ev_tz)
            if until is not None and moment > until:
                break
            produced += 1
            if count is not None and produced > count:
                break
            if cursor >= lo and moment not in exdates:
                out.append(dt.date(cursor.year, cursor.month, cursor.day) if all_day else moment)
        cursor += dt.timedelta(days=1)
    for extra in ev["rdates"]:
        out.append(extra)
    return out


# ------------------------------------------------------------------ the window

def build(text, now, days, tz):
    window_start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + dt.timedelta(days=days)
    parsed, skipped = [], 0
    for props in components(text):
        # One malformed event must not blank the whole calendar: it is skipped and counted.
        try:
            e = parse_event(props, tz)
        except Exception:
            skipped += 1
            continue
        if e:
            parsed.append(e)

    overrides = {}
    for e in parsed:
        if e["recurrence_id"] is not None:
            overrides[(e["uid"], instant(e["recurrence_id"], tz))] = e

    emitted = []
    unsupported = 0

    def add(ev, start, end, recurring):
        s_inst = instant(start, tz)
        e_inst = instant(end, tz)
        if e_inst <= window_start and not (s_inst == e_inst and s_inst >= window_start):
            return
        if s_inst >= window_end:
            return
        if ev["all_day"]:
            s_text = start.isoformat() if not isinstance(start, dt.datetime) else start.date().isoformat()
            e_text = end.isoformat() if not isinstance(end, dt.datetime) else end.date().isoformat()
        else:
            s_text = s_inst.astimezone(tz).isoformat(timespec="seconds")
            e_text = e_inst.astimezone(tz).isoformat(timespec="seconds")
        emitted.append({
            "id": ev["uid"] + ("_" + s_text if recurring else ""),
            "summary": ev["summary"],
            "start": s_text,
            "end": e_text,
            "location": ev["location"],
            "description": ev["description"][:200],
            "colorId": "",
            "recurring": recurring,
            "calendar": "ics",
        })

    for ev in parsed:
        if ev["status"] == "CANCELLED":
            continue
        mark = len(emitted)
        try:
            recurring = bool(ev["rule"] or ev["rdates"]) and ev["recurrence_id"] is None
            if ev["rule"] and not rule_supported(ev["rule"]):
                unsupported += 1
            if ev["recurrence_id"] is not None or not recurring:
                add(ev, ev["start"], ev["end"], ev["recurrence_id"] is not None)
                continue
            span = max(ev["end"] - ev["start"], dt.timedelta(0))
            for start in occurrences(ev, window_start, window_end, tz):
                if (ev["uid"], instant(start, tz)) in overrides:
                    continue  # replaced by an edited instance, emitted on its own
                end = start + span
                add(ev, start, end, True)
        except Exception:  # a bad rule or an out-of-range date (year 9999): this event only
            del emitted[mark:]
            skipped += 1

    # Cancelled instances of a series are listed as overrides with STATUS:CANCELLED
    # and were skipped above; the master occurrence they replace was dropped by
    # the override lookup, which is what removes it.
    emitted.sort(key=lambda e: (e["start"], e["end"]))
    by_date = {}
    for e in emitted:
        by_date.setdefault(e["start"][:10], []).append(e)
    return {
        "as_of": now.astimezone(tz).isoformat(timespec="seconds"),
        "window_days": days,
        "source": "ics",
        "calendars": ["ics"],
        "events_by_date": by_date,
        "skipped": skipped,
    }, unsupported


def read_source(url):
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if url.startswith("webcals://"):
        url = "https://" + url[len("webcals://"):]
    if os.path.exists(url):
        if not os.path.isfile(url):
            raise ValueError("not a regular file")  # a FIFO would block forever
        with open(url, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
    else:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("the URL must start with https:// or webcal://")
        req = urllib.request.Request(url, headers={"User-Agent": "omarchy-agenda/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("the calendar is larger than 25 MB")
    return raw.decode("utf-8", errors="replace")
