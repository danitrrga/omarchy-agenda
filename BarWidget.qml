import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Agenda in the bar: what block is running, how long it has left, and, on
// hover, what comes next.
//
// Same shape as danitrrga.nightguard, which is the shape the shell requires of
// a bar widget that owns a popup: BarWidget as the root type, the panel in a
// separate file behind a Loader, and `opened` / `open()` / `close()` /
// `closeForPopoutSwitch()` forwarded to it. Skipping any of those is what made
// Nightguard's first version freeze the bar.
//
// Read-only. The calendar is edited in Google Calendar (right click) or by
// asking Claude; this only shows it.
//
// Where the calendar comes from is the user's choice, made in the panel's
// Ajustes view and stored in ~/.config/omarchy/agenda/config.json: a secret
// iCal address, their own Google account, or a command of their own. All three
// are read by agenda_ctl.py into one file this plugin owns, day.json, so the
// widget never knows which one it is talking to. Credentials stay in the
// private config; this file only ever sees whether one is stored.
BarWidget {
  id: root
  moduleName: "io.github.danitrrga.agenda"

  // Absolute, derived from HOME: the shell does not inherit the login PATH.
  readonly property string home: Quickshell.env("HOME")
  readonly property string dataPath: home + "/.local/state/omarchy/agenda/day.json"
  readonly property string configPath: home + "/.config/omarchy/agenda/config.json"
  readonly property string pluginFolder: decodeURIComponent(String(Qt.resolvedUrl(".")).replace("file://", "")).replace(/\/$/, "")
  readonly property string ctlPath: decodeURIComponent(String(Qt.resolvedUrl("agenda_ctl.py")).replace("file://", ""))
  readonly property string calendarUrl: "https://calendar.google.com/calendar/u/0/r/day"

  // The saved choice. Secrets are in here (the shell is the user's own
  // process), but nothing outside this widget and Settings.qml reads them, and
  // no IPC answer includes them.
  property var config: ({})
  readonly property string source: config && config.source ? String(config.source) : ""
  property bool forceUnconfigured: false  // for scripted checks of the setup state
  readonly property bool sourceReady: {
    var c = config || {}
    if (source === "ics") return !!(c.ics && c.ics.url)
    if (source === "google") return !!(c.google && c.google.client_id && c.google.client_secret && c.google.refresh_token)
    if (source === "command") return !!(c.command && c.command.line)
    return false
  }
  readonly property bool configured: !forceUnconfigured && sourceReady

  // A command source may name a file whose change means "the calendar moved"
  // (an agent that rewrites a cache), so it refreshes within seconds instead of
  // waiting for the poll.
  readonly property string watchPath: {
    var w = source === "command" && config.command && config.command.watch ? String(config.command.watch) : ""
    return w.indexOf("~/") === 0 ? home + w.slice(1) : w
  }

  // Last good reading. A failed refresh leaves these alone so one bad network
  // minute does not blank the bar.
  property var events: []
  property int failures: 0
  property real asOf: 0
  property bool loaded: false
  property string lastError: ""

  readonly property real nowMs: clock.date.getTime()
  readonly property var face: configured ? Model.faces(events, nowMs) : setupFace
  readonly property var setupFace: ({
    state: "setup",
    main: { kind: "empty", title: "connect your calendar", value: "", urgent: false },
    rest: null, progress: -1, event: null
  })
  readonly property bool stale: loaded && asOf > 0 && nowMs - asOf > 30 * 60000
  readonly property bool refreshing: refreshProc.running
  property int refreshSerial: 0  // bumps when a refresh ends, for the settings view

  // The roll. Hover raises it after a short dwell so crossing the bar does not
  // flicker the label; `forceRoll` is for scripted checks.
  property bool forceRoll: false
  readonly property bool canRoll: face.rest !== null && !vertical
  readonly property bool rolled: canRoll && (forceRoll || rollIntent)
  property bool rollIntent: false

  // The key bound to this plugin, as the names of its keys ("Super", "Shift",
  // "C"), read from Hyprland's own binding table so the panel always shows the
  // key that is actually bound and notices when the user rebinds it. A binding
  // belongs to this plugin when its description starts with "Agenda".
  property var shortcutKeys: []

  function parseBinds(text) {
    var binds
    try { binds = JSON.parse(text || "[]") } catch (e) { return }
    var mods = [[64, "Super"], [4, "Ctrl"], [8, "Alt"], [1, "Shift"]]
    var named = { SEMICOLON: ";", COMMA: ",", PERIOD: ".", SLASH: "/", SPACE: "Space", RETURN: "Enter",
                  ESCAPE: "Esc", MINUS: "-", EQUAL: "=", TAB: "Tab" }
    for (var i = 0; i < binds.length; i++) {
      var bind = binds[i]
      if (String(bind.description || "").indexOf("Agenda") !== 0) continue
      var keys = []
      for (var m = 0; m < mods.length; m++) if (bind.modmask & mods[m][0]) keys.push(mods[m][1])
      var key = String(bind.key || "")
      keys.push(named[key] || (key.length === 1 ? key.toUpperCase() : key.charAt(0) + key.slice(1).toLowerCase()))
      root.shortcutKeys = keys
      return
    }
    root.shortcutKeys = []
  }

  Process {
    id: bindsProc
    command: ["/usr/bin/hyprctl", "binds", "-j"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.parseBinds(text) }
  }

  Component.onCompleted: bindsProc.running = true
  onOpenedChanged: if (opened && !bindsProc.running) bindsProc.running = true

  // --- panel contract (see danitrrga.nightguard for why each is needed) ----

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true : false

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  // --- data -----------------------------------------------------------------

  function ingest(text) {
    var parsed
    try {
      parsed = JSON.parse(text || "")
    } catch (e) {
      return  // unreadable (mid-write or empty): the previous reading stands
    }
    var model = Model.load(parsed)
    if (!parsed || !parsed.events_by_date) return
    root.events = model.events
    root.failures = model.failures
    root.asOf = model.asOf
    root.loaded = true
  }

  // A refresh asked for while one is running (a setting saved a moment after the poll started)
  // is remembered and run when that one ends, not dropped: the running one read the old source.
  property bool refreshQueued: false
  property bool refreshKilled: false

  function refresh() {
    if (!configured) return
    if (refreshProc.running) { root.refreshQueued = true; return }
    root.refreshKilled = false
    refreshProc.running = true
  }

  onConfiguredChanged: refresh()

  // A changed setting (a new address, a different source) means read again.
  Timer { id: configDebounce; interval: 400; onTriggered: root.refresh() }

  FileView {
    id: configFile
    path: root.configPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try {
        var parsed = JSON.parse(text() || "{}")
        // Only an object is a config: `null`, a list or a number would break every binding that reads it.
        root.config = (parsed && typeof parsed === "object" && !Array.isArray(parsed)) ? parsed : ({})
      } catch (e) { root.config = ({}) }
      if (root.configured) configDebounce.restart()
    }
    onLoadFailed: root.config = ({})
  }

  FileView {
    id: file
    path: root.dataPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.ingest(text())
  }

  Process {
    id: refreshProc
    command: ["/usr/bin/python3", "-I", root.ctlPath, "fetch", "--out", root.dataPath]
    stderr: StdioCollector { id: errors; waitForEnd: true }
    // A failed fetch exits non-zero and leaves the previous day.json alone, so
    // the failure is only visible here, in the last line of stderr. Reload
    // explicitly as well: the file is replaced atomically, which swaps the
    // inode a watcher may be holding.
    // Only a failed run has an error to show: a successful one may still print a warning
    // (events it skipped), which is not a failure.
    onExited: function(exitCode, exitStatus) {
      var out = String(errors.text || "").trim()
      if (root.refreshKilled) root.lastError = "reading the calendar took longer than 30 s"
      else root.lastError = (exitCode !== 0 && out.length > 0) ? out.split("\n").pop()
                          : (exitCode !== 0 ? "the refresh failed" : "")
      file.reload()
      root.refreshSerial++
      if (root.refreshQueued) { root.refreshQueued = false; root.refresh() }
    }
  }

  // A fetch that never returns would freeze refreshing for good: a Process
  // that is still running cannot be relaunched.
  Timer { interval: 30000; running: refreshProc.running; onTriggered: { root.refreshKilled = true; refreshProc.running = false } }

  Timer {
    interval: (root.source === "ics" ? 15 : 10) * 60000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  FileView {
    path: root.watchPath
    watchChanges: true
    printErrors: false
    onFileChanged: syncDebounce.restart()
  }
  Timer { id: syncDebounce; interval: 2500; onTriggered: root.refresh() }

  SystemClock {
    id: clock
    precision: SystemClock.Minutes
  }

  Timer { id: rollTimer; interval: 160; onTriggered: root.rollIntent = true }

  // --- the widget slot ------------------------------------------------------

  readonly property real labelWidth: Math.min(Style.space(230),
    Math.max(mainFace.naturalWidth, restFace.naturalWidth))
  // The icon leads the label and does not roll with it. Nerd Font glyph, same
  // family the shell binds `monospace` to, as the other bar widgets use.
  readonly property real iconGap: Style.space(7)
  readonly property real contentWidth: iconText.implicitWidth + iconGap + labelWidth

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "io.github.danitrrga.agenda"

    function refresh(): void { root.refresh() }
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function roll(on: bool): void { root.forceRoll = on }
    function setup(on: bool): void { root.forceUnconfigured = on }
    function move(dy: int): void { if (panelLoader.item) panelLoader.item.keyMove(0, dy) }
    function nav(dx: int, dy: int): void { if (panelLoader.item) panelLoader.item.keyMove(dx, dy) }
    function activate(): void { if (panelLoader.item) panelLoader.item.keyActivate() }
    function scrollState(): string { return panelLoader.item ? panelLoader.item.debug() : "{}" }
    function settings(on: bool): void { if (panelLoader.item) panelLoader.item.setSettings(on) }
    function day(offset: int): void { if (panelLoader.item) panelLoader.item.shiftDay(offset - panelLoader.item.dayOffset) }

    // What the widget computed and is drawing, so a check can compare it with
    // the cache without looking at the screen.
    function state(): string {
      return JSON.stringify({
        opened: root.opened,
        state: root.face.state,
        main: root.face.main,
        rest: root.face.rest,
        rolled: root.rolled,
        hovered: button.tooltipHovered,
        rollIntent: root.rollIntent,
        progress: root.face.progress,
        events: root.events.length,
        failures: root.failures,
        asOf: root.asOf,
        stale: root.stale,
        loaded: root.loaded,
        configured: root.configured,
        source: root.source,
        shortcut: root.shortcutKeys.join("+"),
        pluginFolder: root.pluginFolder,
        refreshing: root.refreshing,
        lastError: root.lastError,
        labelWidth: root.labelWidth,
        buttonWidth: button.implicitWidth,
        panelLoaded: panelLoader.item !== null,
        panelWired: panelLoader.item ? panelLoader.item.hostWidget === root : false,
        screenX: button.mapToGlobal(0, 0).x,
        screenY: button.mapToGlobal(0, 0).y
      })
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // A vertical bar has no room for words: fall back to a glyph that is
    // dimmed when nothing is running.
    labelVisible: root.vertical
    text: root.vertical ? "󰃰" : ""
    hasVisualContent: true
    dimmed: root.loaded && root.face.state === "empty"
    fixedWidth: root.vertical ? -1 : root.contentWidth + scaledHorizontalMargin * 2
    tooltipText: root.stale ? "Calendar not synced" : ""

    onTooltipHoveredChanged: {
      if (tooltipHovered) rollTimer.restart()
      else { rollTimer.stop(); root.rollIntent = false }
    }

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.refresh()
      else if (b === Qt.RightButton) Quickshell.execDetached(["xdg-open", root.calendarUrl])
      else {
        root.toggle()
      }
    }

    // The two faces share one clipped window and swap by sliding, like a
    // departures board: the current block leaves upward, the next arrives from
    // below. Height-sized children, so the slide is exactly one bar.
    Text {
      id: iconText
      visible: !root.vertical
      anchors.left: parent.left
      anchors.leftMargin: button.scaledHorizontalMargin
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "󰃰"
      color: button.foreground
      opacity: root.loaded ? (root.stale ? 0.6 : 1) : 0.5
      font.family: button.fontFamily
      font.pixelSize: Style.bar.iconFont
      renderType: Text.NativeRendering
    }

    Item {
      id: viewport
      visible: !root.vertical
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      anchors.left: iconText.right
      anchors.leftMargin: root.iconGap
      anchors.right: parent.right
      anchors.rightMargin: button.scaledHorizontalMargin
      clip: true

      AgendaFace {
        id: mainFace
        width: parent.width
        height: parent.height
        y: root.rolled ? -height : 0
        face: root.face.main
        foreground: button.foreground
        accent: Color.accent
        urgent: button.activeColor
        fontFamily: button.fontFamily
        maxWidth: Style.space(230)
        opacity: root.loaded ? (root.stale ? 0.6 : 1) : 0.5

        Behavior on y { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
      }

      AgendaFace {
        id: restFace
        width: parent.width
        height: parent.height
        y: root.rolled ? 0 : height
        face: root.face.rest
        foreground: button.foreground
        accent: Color.accent
        urgent: button.activeColor
        fontFamily: button.fontFamily
        maxWidth: Style.space(230)

        Behavior on y { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
      }
    }

    // Elapsed share of the running block, along the foot of the whole widget
    // (edge to edge, so it reads as the widget's own progress and not as an
    // underline of the text): the same device Nightguard uses for tokens, a
    // quantity that is visibly finite.
    Rectangle {
      visible: !root.vertical && root.face.progress >= 0 && !root.rolled
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.bottomMargin: Math.max(1, Style.space(2))
      height: Math.max(1, Style.space(1))
      color: Qt.rgba(button.foreground.r, button.foreground.g, button.foreground.b, 0.2)

      Rectangle {
        height: parent.height
        width: parent.width * Math.max(0, root.face.progress)
        color: button.foreground
        opacity: 0.8
      }
    }
  }
}
