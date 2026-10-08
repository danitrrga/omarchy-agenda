#!/usr/bin/env python3
"""A made-up week for screenshots, in the format the agenda's `command` source reads.

Today's blocks are placed relative to now, so one of them is always running when a screenshot is
taken; the other days follow a fixed pattern. Nothing here comes from a real calendar, which is the
point: README images must not show anyone's actual schedule.

    python3 tools/demo_events.py        # prints the JSON document on stdout
"""
import datetime as dt
import json

NOW = dt.datetime.now().astimezone().replace(second=0, microsecond=0)
NOW = NOW.replace(minute=NOW.minute - NOW.minute % 5)
MIDNIGHT = NOW.replace(hour=0, minute=0)

# (minutes from now to start, length in minutes, title, location, description)
TODAY = [
    (-300, 60, "Morning run", "", ""),
    (-215, 20, "Breakfast", "", ""),
    (-180, 90, "Deep work: parser", "", "Finish the recurrence expander and its tests."),
    (-75, 15, "Stand-up", "Meet", ""),
    (-40, 90, "Design review", "Room 2.14", "Walk through the settings flow with the team. Bring the three source options."),
    (65, 30, "Lunch", "", ""),
    (120, 45, "Write docs", "", ""),
    (190, 60, "Gym", "", ""),
    (270, 30, "Dinner", "", ""),
]
WEEK = [
    (6, 30, 45, "Morning run"), (8, 0, 120, "Deep work"), (10, 30, 15, "Stand-up"),
    (11, 0, 60, "Planning"), (12, 30, 45, "Lunch"), (14, 0, 90, "Pairing session"),
    (16, 0, 60, "Gym"), (19, 0, 30, "Dinner"),
]


def iso(d):
    return d.isoformat(timespec="seconds")


def event(i, title, start, length, location="", description=""):
    return {"id": "demo-%s-%d" % (start.strftime("%m%d"), i), "summary": title, "start": iso(start),
            "end": iso(start + dt.timedelta(minutes=length)), "location": location,
            "description": description, "recurring": False}


days = {}
for i, (offset, length, title, location, description) in enumerate(TODAY):
    start = NOW + dt.timedelta(minutes=offset)
    days.setdefault(start.strftime("%Y-%m-%d"), []).append(event(i, title, start, length, location, description))
for d in range(1, 7):
    day = MIDNIGHT + dt.timedelta(days=d)
    for i, (h, m, length, title) in enumerate(WEEK):
        if d % 3 == 0 and title == "Gym":
            continue
        start = day.replace(hour=h, minute=m)
        days.setdefault(start.strftime("%Y-%m-%d"), []).append(event(i, title, start, length))
days.setdefault((MIDNIGHT + dt.timedelta(days=2)).strftime("%Y-%m-%d"), []).append(
    {"id": "demo-allday", "summary": "Conference day", "start": (MIDNIGHT + dt.timedelta(days=2)).strftime("%Y-%m-%d"),
     "end": (MIDNIGHT + dt.timedelta(days=3)).strftime("%Y-%m-%d")})
for events in days.values():
    events.sort(key=lambda e: e["start"])

print(json.dumps({"as_of": iso(NOW), "window_days": 7, "source": "demo", "events_by_date": days}))
