.pragma library

// Pure calendar logic for the agenda widget: no QML, no clock of its own, so
// every function takes `now` and can be exercised from node with a fixed time.
//
// The payload is what refresh_calendar_cache.py writes: events grouped under
// the date they START on. That grouping is only a transport detail. A block
// that starts at 21:00 and ends 01:00 is filed under yesterday but belongs on
// today's timeline too, so everything is flattened here and re-sliced by
// overlap with the day being drawn.

var DAY_MS = 24 * 60 * 60 * 1000
var WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
var WEEKDAYS_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

// A date-only value ("2026-10-09") is an all-day event. new Date() reads it as
// UTC midnight, which lands at 02:00 local and then sorts into the timeline as
// if it were a 2 a.m. block, so the shape is checked before the parser is.
function isDateOnly(s) { return typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s) }

function parseMoment(s) {
  if (isDateOnly(s)) {
    var p = s.split("-")
    var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]))
    // "2026-02-30" would roll into March: not a date, so not an event.
    return d.getMonth() === Number(p[1]) - 1 && d.getDate() === Number(p[2]) ? d.getTime() : NaN
  }
  return new Date(s).getTime()
}

// Returns { events, failures, asOf }. An entry without a start is the
// refresher's `{"error": ...}` marker for a calendar it could not read; it is
// counted, never drawn, because a calendar that failed to load must show up
// as a degraded state and not as a free afternoon.
function load(payload) {
  var out = { events: [], failures: 0, asOf: 0 }
  if (!payload || typeof payload !== "object") return out
  out.asOf = payload.as_of ? new Date(payload.as_of).getTime() : 0
  var byDate = payload.events_by_date || {}
  for (var key in byDate) {
    var list = byDate[key]
    if (!list || !list.length) continue
    for (var i = 0; i < list.length; i++) {
      var e = list[i]
      if (!e || e.error || !e.start) { out.failures++; continue }
      var allDay = isDateOnly(e.start)
      var start = parseMoment(e.start)
      var end = e.end ? parseMoment(e.end) : start
      if (isNaN(start) || isNaN(end)) { out.failures++; continue }
      out.events.push({
        id: String(e.id || (key + i)),
        title: String(e.summary || "(untitled)"),
        start: start,
        // An all-day event with no usable end still fills its day.
        end: end > start ? end : (allDay ? addDays(start, 1) : start),
        allDay: allDay,
        location: String(e.location || ""),
        description: String(e.description || ""),
        calendar: String(e.calendar || ""),
        recurring: !!e.recurring
      })
    }
  }
  out.events.sort(function(a, b) { return a.start - b.start || a.end - b.end })
  return out
}

function dayStart(ms) {
  var d = new Date(ms)
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

// Local midnight + 24h is wrong twice a year; stepping the calendar day is not.
function addDays(dayStartMs, n) {
  var d = new Date(dayStartMs)
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n).getTime()
}

function overlapsDay(e, dayStartMs) {
  var next = addDays(dayStartMs, 1)
  if (e.allDay) return e.start < next && e.end > dayStartMs
  // A zero-length block still belongs to the day it sits on.
  return e.start < next && (e.end > dayStartMs || e.start >= dayStartMs)
}

function timedForDay(events, dayStartMs) {
  return events.filter(function(e) { return !e.allDay && overlapsDay(e, dayStartMs) })
}

function allDayForDay(events, dayStartMs) {
  return events.filter(function(e) { return e.allDay && overlapsDay(e, dayStartMs) })
}

// What is happening right now. Overlaps are real (a 15-minute "shower" can sit
// inside a long block), and the one that started last is the one the person
// is actually in; ties go to whichever ends first.
function current(events, now) {
  var best = null
  for (var i = 0; i < events.length; i++) {
    var e = events[i]
    if (e.allDay || !(e.start <= now && now < e.end)) continue
    if (!best || e.start > best.start || (e.start === best.start && e.end < best.end)) best = e
  }
  return best
}

// The next block to begin, skipping whatever is current. Search is not limited
// to today: at 22:30 the useful answer is tomorrow's 05:00, not "nothing".
function next(events, now) {
  for (var i = 0; i < events.length; i++) {
    var e = events[i]
    if (!e.allDay && e.start > now) return e
  }
  return null
}

function pad(n) { return (n < 10 ? "0" : "") + n }

function clock(ms) {
  var d = new Date(ms)
  return pad(d.getHours()) + ":" + pad(d.getMinutes())
}

// Time left, as compact as a bar allows: "1h12", "2h", "25m", "<1m".
// Rounded UP to the minute so a block that ends at 21:30 reads "1m" at 21:29:10
// and never "0m" while still running.
function span(ms) {
  var minutes = Math.ceil(Math.max(0, ms) / 60000)
  if (minutes < 1) return "<1m"
  if (minutes < 60) return minutes + "m"
  var h = Math.floor(minutes / 60)
  var m = minutes % 60
  return m === 0 ? h + "h" : h + "h" + pad(m)
}

function untilStart(e, now) { return span(e.start - now) }

function progress(e, now) {
  var total = e.end - e.start
  if (total <= 0) return 0
  return Math.min(1, Math.max(0, (now - e.start) / total))
}

// "today", "tomorrow", or the weekday; used when the next block is not today.
function dayWord(dayStartMs, now) {
  var today = dayStart(now)
  if (dayStartMs === today) return "today"
  if (dayStartMs === addDays(today, 1)) return "tomorrow"
  return WEEKDAYS_LONG[new Date(dayStartMs).getDay()]
}

function weekdayShort(dayStartMs) { return WEEKDAYS[new Date(dayStartMs).getDay()] }
function dayNumber(dayStartMs) { return new Date(dayStartMs).getDate() }

function dayTitle(dayStartMs, now) {
  var d = new Date(dayStartMs)
  var tail = d.getDate() + " " + MONTHS[d.getMonth()]
  var word = dayWord(dayStartMs, now)
  return (word === "today" || word === "tomorrow" ? word : WEEKDAYS_LONG[d.getDay()]) + " · " + tail
}

// How old a reading is once it is too old to trust, as a marker for the bar: "" while it is fresh
// (30 minutes or less, or there is no usable reading), else "45m", "3h", "2d", or "9d+" past ten days.
// Three characters at most, because the marker takes the icon's place and the bar must not change
// width. Floored, so "59m" never shows as "60m".
var STALE_MS = 30 * 60000

function staleAge(asOf, now) {
  if (!(asOf > 0) || !(now - asOf > STALE_MS)) return ""
  var age = now - asOf
  if (age < 60 * 60000) return Math.floor(age / 60000) + "m"
  if (age < DAY_MS) return Math.floor(age / (60 * 60000)) + "h"
  if (age < 10 * DAY_MS) return Math.floor(age / DAY_MS) + "d"
  return "9d+"
}

// The bar's two faces. `rest` is the face shown while hovering and may be
// null when there is nothing worth rolling to. Each face is plain data so the
// widget only lays it out and a test can read it without a display.
//
//   in a block      main: "kickboxing" + "1h12" left     rest: next block + "in 1h12"
//   between blocks  main: next block + "in 25m"          rest: the one after it
//   nothing today   main: "MEDITATE tomorrow 05:00"       rest: null
//   no data         main: "no blocks"
function faces(events, now) {
  var cur = current(events, now)
  var nxt = next(events, now)
  var todayStart = dayStart(now)

  function nextFace(e) {
    if (!e) return null
    var sameDay = dayStart(e.start) === todayStart
    return {
      kind: "next",
      title: e.title,
      value: sameDay ? "in " + untilStart(e, now) : dayWord(dayStart(e.start), now) + " " + clock(e.start),
      urgent: sameDay && e.start - now <= 10 * 60000
    }
  }

  if (cur) {
    return {
      state: "now",
      main: { kind: "now", title: cur.title, value: span(cur.end - now), urgent: cur.end - now <= 5 * 60000 },
      rest: nextFace(nxt),
      progress: progress(cur, now),
      event: cur
    }
  }
  if (nxt) {
    var after = null
    for (var i = 0; i < events.length; i++) {
      if (!events[i].allDay && events[i].start > nxt.start) { after = events[i]; break }
    }
    return { state: "next", main: nextFace(nxt), rest: nextFace(after), progress: -1, event: nxt }
  }
  return { state: "empty", main: { kind: "empty", title: "no blocks", value: "", urgent: false }, rest: null, progress: -1, event: null }
}

// Share of the waking day (16 h) that has something scheduled, 0..1, for the
// day strip's occupancy bar. Overlapping blocks are merged first: two lectures
// that run in parallel fill the day once, not twice.
function busyShare(events, dayStartMs) {
  var from = dayStartMs
  var to = addDays(dayStartMs, 1)
  var spans = timedForDay(events, dayStartMs).map(function(e) {
    return [Math.max(e.start, from), Math.min(e.end, to)]
  }).filter(function(p) { return p[1] > p[0] })
  spans.sort(function(a, b) { return a[0] - b[0] })
  var total = 0
  var curStart = -Infinity   // not -1: that is a real instant, 1969-12-31T23:59:59.999Z
  var curEnd = -Infinity
  for (var i = 0; i < spans.length; i++) {
    if (spans[i][0] > curEnd) {
      if (curEnd > curStart) total += curEnd - curStart
      curStart = spans[i][0]
      curEnd = spans[i][1]
    } else if (spans[i][1] > curEnd) {
      curEnd = spans[i][1]
    }
  }
  if (curEnd > curStart) total += curEnd - curStart
  return Math.min(1, total / (16 * 60 * 60000))
}

// Free time worth naming: long enough to use, short enough to be a gap inside
// a day rather than the stretch outside it (nobody is "free" from 01:00 to 13:30).
var GAP_MIN_MS = 15 * 60000
var GAP_MAX_MS = 6 * 60 * 60000

// Row model for one day's timeline. Three kinds of row:
//   event  a block, flagged past / live, clipped to the day
//   gap    free time between two blocks, so the empty stretches are as legible
//          as the full ones
//   now    the current-time line, only when nothing is running: while a block
//          is live the block itself carries "now", and a second line would
//          say the same thing twice
// Gaps are measured from the latest end so far, so a block that runs inside
// another does not invent free time.
function timeline(events, dayStartMs, now) {
  var rows = []
  var isToday = dayStartMs === dayStart(now)
  var list = timedForDay(events, dayStartMs)
  var cursorEnd = -Infinity
  var anyLive = false
  var nowInGap = false
  var dayEnd = addDays(dayStartMs, 1)
  for (var i = 0; i < list.length; i++) {
    var e = list[i]
    var from = Math.max(e.start, dayStartMs)
    if (i > 0 && from - cursorEnd >= GAP_MIN_MS && from - cursorEnd <= GAP_MAX_MS) {
      var inside = isToday && cursorEnd <= now && now < from
      if (inside) nowInGap = true
      rows.push({ type: "gap", from: cursorEnd, to: from, nowInside: inside })
    }
    var live = isToday && e.start <= now && now < e.end
    if (live) anyLive = true
    rows.push({
      type: "event",
      event: e,
      past: isToday && e.end <= now,
      live: live,
      // Cut to the day: the tail of an overnight block should say 00:00, not
      // repeat the previous evening's start.
      from: from,
      to: Math.min(e.end, dayEnd),
      progress: live ? progress(e, now) : 0
    })
    cursorEnd = Math.max(cursorEnd, Math.min(e.end, dayEnd))
  }
  if (isToday && !anyLive && !nowInGap && list.length > 0) {
    var at = rows.length
    for (var j = 0; j < rows.length; j++) {
      if (rows[j].type === "event" && rows[j].event.start > now) { at = j; break }
    }
    rows.splice(at, 0, { type: "now", at: now })
  }
  // The first block still to come today is the one the eye wants: flag it.
  if (isToday) {
    for (var k = 0; k < rows.length; k++) {
      if (rows[k].type === "event" && rows[k].event.start > now) { rows[k].upNext = true; break }
    }
  }
  return rows
}
