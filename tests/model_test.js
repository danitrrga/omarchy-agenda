// Run from the plugin folder: TZ=Europe/Amsterdam node tests/model_test.js Model.js
const fs = require('fs'), vm = require('vm'), assert = require('assert')
function loadModel(file) {
  const src = fs.readFileSync(file, 'utf8').replace(/^\.pragma library\s*$/m, '')
  const ctx = {}; vm.createContext(ctx); vm.runInContext(src, ctx); return ctx
}
const M = loadModel(process.argv[2])
const T = (s) => new Date(s).getTime()
const ev = (id, summary, start, end, extra = {}) => Object.assign({ id, summary, start, end, calendar: "primary" }, extra)
const payload = { as_of: "2026-10-08T16:06:06+02:00", events_by_date: {
  "2026-10-07": [ev("night", "party", "2026-10-07T21:00:00+02:00", "2026-10-08T01:00:00+02:00")],
  "2026-10-08": [ev("a", "gym", "2026-10-08T13:30:00+02:00", "2026-10-08T14:15:00+02:00"),
                 ev("b", "yt", "2026-10-08T16:00:00+02:00", "2026-10-08T17:30:00+02:00"),
                 ev("c", "admin", "2026-10-08T18:30:00+02:00", "2026-10-08T18:50:00+02:00"),
                 { error: "academic: boom" }],
  "2026-10-09": [ev("d", "MEDITATE", "2026-10-09T05:00:00+02:00", "2026-10-09T05:15:00+02:00"),
                 ev("e", "cumple", "2026-10-09", "2026-10-10")] } }
const L = M.load(payload)
assert.strictEqual(L.failures, 1, 'error entry counted, not drawn')
assert.strictEqual(L.events.length, 6)
const now = T("2026-10-08T16:06:06+02:00")
const allDay = L.events.find(e => e.id === "e")
assert.ok(allDay.allDay)
assert.strictEqual(M.clock(allDay.start), "00:00", "all-day starts at local midnight, not 02:00")
const oct9 = M.dayStart(T("2026-10-09T12:00:00+02:00"))
assert.strictEqual(M.timedForDay(L.events, oct9).length, 1, "all-day not in timeline")
assert.strictEqual(M.allDayForDay(L.events, oct9).length, 1)
const today = M.dayStart(now)
const rows = M.timeline(L.events, today, now)
assert.strictEqual(rows[0].event.id, "night"); assert.strictEqual(M.clock(rows[0].to), "01:00"); assert.ok(rows[0].past)
const f = M.faces(L.events, now)
assert.strictEqual(f.state, "now"); assert.strictEqual(f.main.title, "yt"); assert.strictEqual(f.main.value, "1h24")
assert.strictEqual(f.rest.title, "admin"); assert.strictEqual(f.rest.value, "in 2h24")
const kinds = rows.map(r => r.type === "now" ? "NOW" : r.type === "gap" ? "GAP" : r.event.id)
assert.strictEqual(kinds.join(","), "night,a,GAP,b,GAP,c", "live block carries now: no separate marker")
const g = M.faces(L.events, T("2026-10-08T17:40:00+02:00"))
assert.strictEqual(g.state, "next"); assert.strictEqual(g.main.value, "in 50m")
assert.strictEqual(g.rest.title, "MEDITATE"); assert.strictEqual(g.rest.value, "tomorrow 05:00")
// now inside a gap: carried by the gap row, not a separate marker; upNext flagged
const gapRows = M.timeline(L.events, today, T("2026-10-08T17:40:00+02:00"))
assert.strictEqual(gapRows.map(r => r.type === "now" ? "NOW" : r.type === "gap" ? (r.nowInside ? "GAP*" : "GAP") : r.event.id).join(","), "night,a,GAP,b,GAP*,c")
assert.ok(gapRows.find(r => r.type === "event" && r.event.id === "c").upNext)
// now between two blocks closer than the gap threshold: marker row, in place
const tight = M.load({ as_of: payload.as_of, events_by_date: { "2026-10-08": [ev("x","x","2026-10-08T10:00:00+02:00","2026-10-08T10:30:00+02:00"), ev("y","y","2026-10-08T10:35:00+02:00","2026-10-08T11:00:00+02:00")] } })
assert.strictEqual(M.timeline(tight.events, today, T("2026-10-08T10:32:00+02:00")).map(r => r.type === "now" ? "NOW" : r.type === "gap" ? "GAP" : r.event.id).join(","), "x,NOW,y")
// overlapping blocks invent no free time
const ovl = M.load({ as_of: payload.as_of, events_by_date: { "2026-10-09": [ev("p","p","2026-10-09T08:45:00+02:00","2026-10-09T10:45:00+02:00"), ev("q","q","2026-10-09T08:45:00+02:00","2026-10-09T12:45:00+02:00"), ev("r","r","2026-10-09T10:45:00+02:00","2026-10-09T11:00:00+02:00")] } })
assert.strictEqual(M.timeline(ovl.events, oct9, now).filter(r => r.type === "gap").length, 0)
// a short block inside a long one must not reopen a gap after the long one ends
const nest = M.load({ as_of: payload.as_of, events_by_date: { "2026-10-09": [ev("A","A","2026-10-09T08:00:00+02:00","2026-10-09T12:00:00+02:00"), ev("B","B","2026-10-09T09:00:00+02:00","2026-10-09T09:30:00+02:00"), ev("C","C","2026-10-09T12:10:00+02:00","2026-10-09T13:00:00+02:00")] } })
assert.strictEqual(M.timeline(nest.events, oct9, now).filter(r => r.type === "gap").length, 0, "nested block must not invent free time")
// busy share: overlap merged (08:45-12:45 = 4h of 16h), disjoint spans add
assert.ok(Math.abs(M.busyShare(ovl.events, oct9) - 4 / 16) < 1e-9)
assert.strictEqual(M.busyShare([], oct9), 0)
const late = M.faces(L.events, T("2026-10-08T22:30:00+02:00"))
assert.strictEqual(late.main.title, "MEDITATE"); assert.strictEqual(late.main.value, "tomorrow 05:00"); assert.strictEqual(late.rest, null)
assert.strictEqual(M.faces([], now).state, "empty"); assert.strictEqual(M.faces(M.load(null).events, now).state, "empty")
assert.strictEqual(M.span(29 * 60000 + 10000), "30m"); assert.strictEqual(M.span(10000), "1m"); assert.strictEqual(M.span(0), "<1m")
assert.strictEqual(M.span(3600000), "1h"); assert.strictEqual(M.span(3660000), "1h01")
assert.ok(M.faces(L.events, T("2026-10-08T17:27:00+02:00")).main.urgent)
const d25 = M.dayStart(T("2026-10-25T12:00:00+01:00")); assert.strictEqual(M.clock(M.addDays(d25, 1)), "00:00")
// Found by the 2026-10-08 fuzz pass. A sentinel of -1 is a real instant: a day before 1970 lost its gaps.
{
  const old = M.load({ events_by_date: { x: [ev("a", "a", "1969-07-20T08:00:00", "1969-07-20T09:00:00"),
                                             ev("b", "b", "1969-07-20T10:00:00", "1969-07-20T11:00:00")] } }).events
  const ds = M.dayStart(T("1969-07-20T12:00:00"))
  assert.strictEqual(M.busyShare(old, ds), 2 / 16, "busyShare before 1970")
  const k = M.timeline(old, ds, T("1969-07-20T09:30:00")).map(r => r.type === "now" ? "NOW" : r.type === "gap" ? "GAP" : r.event.id)
  assert.strictEqual(k.join(","), "a,GAP,b", "gap row and no marker inside it, before 1970")
}
// An all-day event with no end, or an end that is not after its start, still fills its own day.
{
  const d = M.dayStart(T("2026-10-09T12:00:00"))
  const ids = M.allDayForDay(M.load({ events_by_date: { "2026-10-09": [
    { id: "noend", summary: "n", start: "2026-10-09" }, { id: "same", summary: "s", start: "2026-10-09", end: "2026-10-09" },
    { id: "ok", summary: "o", start: "2026-10-09", end: "2026-10-10" }] } }).events, d).map(e => e.id).sort()
  assert.deepStrictEqual(Array.from(ids), ["noend", "ok", "same"])
}
// A date that does not exist is a failed entry, not a silent roll into the next month.
{
  const L2 = M.load({ events_by_date: { "2026-02-30": [{ id: "z", summary: "z", start: "2026-02-30", end: "2026-03-01" }] } })
  assert.strictEqual(L2.events.length, 0); assert.strictEqual(L2.failures, 1)
}
// Stale data must say so on the bar itself: a compact age, never more than three characters so the
// marker fits the icon slot, and empty while the reading is fresh (the threshold is the widget's 30 min).
{
  // Red before the change: TypeError: M.staleAge is not a function
  const asOf = T("2026-10-23T14:55:00+02:00")
  const at = ms => asOf + ms
  const MIN = 60000, H = 60 * MIN, D = 24 * H
  assert.strictEqual(M.staleAge(asOf, at(0)), "", "fresh")
  assert.strictEqual(M.staleAge(asOf, at(30 * MIN)), "", "exactly 30 min is still fresh")
  assert.strictEqual(M.staleAge(asOf, at(30 * MIN + 1)), "30m", "just past the threshold")
  assert.strictEqual(M.staleAge(asOf, at(59 * MIN + 59000)), "59m", "minutes are floored, never rounded up to 60m")
  assert.strictEqual(M.staleAge(asOf, at(H)), "1h")
  assert.strictEqual(M.staleAge(asOf, at(23 * H + 59 * MIN)), "23h")
  assert.strictEqual(M.staleAge(asOf, at(D)), "1d")
  assert.strictEqual(M.staleAge(asOf, at(25 * H)), "1d")
  assert.strictEqual(M.staleAge(asOf, at(9 * D + 23 * H)), "9d")
  assert.strictEqual(M.staleAge(asOf, at(10 * D)), "9d+", "capped at three characters")
  assert.strictEqual(M.staleAge(asOf, at(400 * D)), "9d+")
  assert.strictEqual(M.staleAge(0, T("2026-10-24T16:04:00+02:00")), "", "no reading yet is not stale")
  assert.strictEqual(M.staleAge(NaN, T("2026-10-24T16:04:00+02:00")), "", "an unparseable as_of is not stale")
  assert.strictEqual(M.staleAge(at(2 * H), asOf), "", "an as_of in the future (clock skew) is not stale")
  // the real case: 2026-10-23 14:55 read at 2026-10-24 16:04 is a day old
  assert.strictEqual(M.staleAge(asOf, T("2026-10-24T16:04:00+02:00")), "1d")
}
console.log("ALL PASS")
