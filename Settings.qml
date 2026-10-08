import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Where the calendar comes from: three ways, chosen here, no file editing.
//
//   iCal    a secret address; for people who want it to just work
//   Google  their own Google account, through a client they create themselves
//   Script  their own program, for people who already run a sync or an agent
//
// This view only collects and saves. Every credential goes to agenda_ctl.py
// through an environment variable and is written to a private file by it; the
// form never reads one back, it only learns that one is stored.
Item {
  id: root

  property var host: null          // the Panel
  readonly property var widget: host ? host.hostWidget : null
  readonly property var config: host ? host.config : ({})
  readonly property var shortcutKeys: host ? host.shortcutKeys : []
  readonly property color foreground: host ? host.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color urgent: host ? host.urgent : Color.urgent
  readonly property string fontFamily: host ? host.fontFamily : Style.font.family

  property string mode: "ics"
  property string status: ""
  property bool statusBad: false
  property bool busy: false
  property string authUrl: ""
  property bool awaitingRefresh: false
  property string patchJson: "{}"

  readonly property bool fieldFocused: icsField.activeFocus || cidField.activeFocus
    || secField.activeFocus || cmdField.activeFocus || watchField.activeFocus

  readonly property bool hasIcs: !!(config.ics && config.ics.url)
  readonly property bool hasClient: !!(config.google && config.google.client_id && config.google.client_secret)
  readonly property bool hasSession: !!(config.google && config.google.refresh_token)
  readonly property bool hasCommand: !!(config.command && config.command.line)

  readonly property var blurbs: ({
    ics: "Paste your calendar's secret iCal address. In Google Calendar: Settings, your calendar, Integrate calendar. Works with any provider. Google refreshes it slowly, sometimes hours.",
    google: "Your own account, with nobody in between. Create a desktop OAuth client in Google Cloud, enable the Calendar API and paste its ID and secret here. Read-only and instant. If the app is in Testing mode the session expires after 7 days: publish it as In production.",
    command: "For people who already have their own system. The command prints a JSON with events_by_date on stdout, or writes it to the path that replaces {out}. The format is in the README."
  })

  function load() {
    root.mode = root.modes.indexOf(String(config.source)) >= 0 ? String(config.source) : "ics"
    icsField.text = ""
    cidField.text = ""
    secField.text = ""
    cmdField.text = config.command && config.command.line ? String(config.command.line) : ""
    watchField.text = config.command && config.command.watch ? String(config.command.watch) : ""
    if (!root.busy) {  // a sign-in still waiting keeps its status and its fallback link
      root.status = ""
      root.authUrl = ""
    }
  }

  onVisibleChanged: if (visible) load()

  // ---- keyboard navigation ---------------------------------------------------
  // Everything is reachable with the arrows (or h j k l): up and down walk a
  // single column, the source tabs at its head and the controls below it, and
  // left and right on the tabs switch the source. A text field takes the cursor
  // for real, so the field's own keys keep working; up and down still leave it.
  readonly property var modes: ["ics", "google", "command"]
  readonly property var textControls: ["ics", "cid", "sec", "cmd", "watch"]
  property int zone: 1   // 0 is the tabs; 1.. index into `order`
  readonly property var order: root.mode === "ics" ? ["ics", "save", "agent"]
    : (root.mode === "google" ? ["cid", "sec", "login", "save", "agent"] : ["cmd", "watch", "save", "agent"])
  readonly property string zoneName: root.zone > 0 ? root.order[root.zone - 1] : ""

  function control(name) {
    return ({ ics: icsField, cid: cidField, sec: secField, cmd: cmdField, watch: watchField,
              login: loginBtn, save: saveBtn, agent: agentBtn })[name]
  }

  function focusZone() {
    if (root.textControls.indexOf(root.zoneName) >= 0) root.control(root.zoneName).forceActiveFocus()
    else if (root.host) root.host.takeKeys()
  }

  function navigate(dx, dy) {
    if (dy !== 0) {
      root.zone = Math.max(0, Math.min(root.order.length, root.zone + dy))
      root.focusZone()
    } else if (root.zone === 0 && dx !== 0) {
      root.mode = root.modes[(root.modes.indexOf(root.mode) + dx + root.modes.length) % root.modes.length]
      root.status = ""
    }
  }

  function activate() {
    if (root.zone === 0) { root.zone = 1; root.focusZone(); return }
    if (root.zoneName === "login") root.save(true)
    else if (root.zoneName === "save") root.save(false)
    else if (root.zoneName === "agent") root.askAgent()
  }

  function focusFirst() {
    root.zone = 1
    root.focusZone()
  }

  function patch() {
    var p = { source: root.mode }
    if (root.mode === "ics") p.ics = { url: icsField.text.trim() }
    else if (root.mode === "google") p.google = { client_id: cidField.text.trim(), client_secret: secField.text.trim() }
    else p.command = { line: cmdField.text.trim(), watch: watchField.text.trim() }
    return JSON.stringify(p)
  }

  readonly property bool ready: {
    if (root.mode === "ics") return icsField.text.trim() !== "" || root.hasIcs
    if (root.mode === "google") return (cidField.text.trim() !== "" || root.hasClient) && (secField.text.trim() !== "" || root.hasClient)
    return cmdField.text.trim() !== ""
  }

  // The prompt is plain text on purpose: it goes through argv, so it carries no
  // credential and nothing the agent cannot read in the open.
  function askAgent() {
    var folder = root.widget ? root.widget.pluginFolder : ""
    Quickshell.execDetached(["/usr/bin/omarchy", "agent", "prompt",
      "Set up the Omarchy Agenda widget for me. First read " + folder + "/SETUP-WITH-AGENT.md and follow it exactly. "
      + "Ask me which calendar I use and how I want to connect it: a secret iCal address, my own Google account, or my own script."])
    if (root.host) root.host.close()
  }

  function save(thenLogin) {
    if (root.busy || !root.ready) return
    root.busy = true
    root.status = "saving…"
    root.statusBad = false
    root.patchJson = root.patch()
    root.loginAfterSave = thenLogin === true
    saveProc.running = true
  }

  property bool loginAfterSave: false

  Process {
    id: saveProc
    command: ["/usr/bin/python3", "-I", root.widget ? root.widget.ctlPath : "", "config-set"]
    environment: ({ AGENDA_PATCH: root.patchJson })
    stderr: StdioCollector { id: saveErr; waitForEnd: true }
    onExited: function(code) {
      if (code !== 0) {
        root.busy = false
        root.statusBad = true
        root.status = String(saveErr.text || "could not save").trim().split("\n").pop()
        return
      }
      if (root.loginAfterSave) {
        root.status = "waiting for the browser…"
        loginProc.running = true
      } else {
        root.status = "reading the calendar…"
        root.awaitingRefresh = true
        if (root.widget) root.widget.refresh()
        else root.busy = false
      }
    }
  }

  Process {
    id: loginProc
    command: ["/usr/bin/python3", "-I", root.widget ? root.widget.ctlPath : "", "google-login"]
    stdout: SplitParser {
      onRead: function(line) { if (line.indexOf("AUTH_URL ") === 0) root.authUrl = line.slice(9) }
    }
    stderr: StdioCollector { id: loginErr; waitForEnd: true }
    onExited: function(code) {
      root.authUrl = ""
      if (code === 0) {
        root.status = "signed in, reading the calendar…"
        root.statusBad = false
        root.awaitingRefresh = true
        if (root.widget) root.widget.refresh()
        else root.busy = false
      } else {
        root.busy = false
        root.statusBad = true
        root.status = String(loginErr.text || "sign-in failed").trim().split("\n").pop()
      }
    }
  }

  // The refresh the save started has finished: report what it found, or why not.
  Connections {
    target: root.widget
    function onRefreshSerialChanged() {
      if (!root.awaitingRefresh) return
      root.awaitingRefresh = false
      root.busy = false
      if (root.widget.lastError !== "") {
        root.statusBad = true
        root.status = root.widget.lastError
      } else {
        root.statusBad = false
        root.status = root.widget.events.length + (root.widget.events.length === 1 ? " event read" : " events read")
      }
    }
  }

  Column {
    anchors.fill: parent
    spacing: Style.space(8)

    Item {
      width: parent.width
      height: heading.implicitHeight

      Text {
        id: heading
        textFormat: Text.PlainText
        text: "WHERE THE CALENDAR COMES FROM"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
        font.letterSpacing: 1.2
      }

      // The way out of settings, without needing a key: with a field focused
      // every key belongs to the field.
      Text {
        visible: root.host && root.host.configured
        anchors.right: parent.right
        textFormat: Text.PlainText
        text: "back"
        color: backMouse.containsMouse ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true

        MouseArea {
          id: backMouse
          anchors.fill: parent
          anchors.margins: -Style.space(4)
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: if (root.host) root.host.setSettings(false)
        }
      }
    }

    ButtonGroup {
      options: [ { value: "ics", label: "iCal" }, { value: "google", label: "Google" }, { value: "command", label: "Script" } ]
      value: root.mode
      foreground: root.foreground
      fontFamily: root.fontFamily
      focusable: false
      cursorIndex: root.zone === 0 ? root.modes.indexOf(root.mode) : -1
      onChanged: function(v) {
        root.mode = v; root.status = ""; root.zone = 0
        // A click on a tab hides the field that had the keyboard: hand the keys back, or none work.
        if (root.host) root.host.takeKeys()
      }
    }

    Text {
      width: parent.width
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      text: root.blurbs[root.mode]
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    // ---- iCal ---------------------------------------------------------------
    TextField {
      id: icsField
      visible: root.mode === "ics"
      width: parent.width
      password: true
      placeholderText: root.hasIcs ? "address saved (leave empty to keep it)" : "https://calendar.google.com/…/basic.ics"
      foreground: root.foreground
      font.family: root.fontFamily
      Keys.onEscapePressed: { if (root.host) root.host.close() }
      Keys.onUpPressed: function(e) { root.navigate(0, -1); e.accepted = true }
      Keys.onDownPressed: function(e) { root.navigate(0, 1); e.accepted = true }
      onAccepted: root.save(false)
    }

    // ---- Google -------------------------------------------------------------
    TextField {
      id: cidField
      visible: root.mode === "google"
      width: parent.width
      placeholderText: root.hasClient ? "client ID saved" : "client ID (….apps.googleusercontent.com)"
      foreground: root.foreground
      font.family: root.fontFamily
      Keys.onEscapePressed: { if (root.host) root.host.close() }
      Keys.onUpPressed: function(e) { root.navigate(0, -1); e.accepted = true }
      Keys.onDownPressed: function(e) { root.navigate(0, 1); e.accepted = true }
    }
    TextField {
      id: secField
      visible: root.mode === "google"
      width: parent.width
      password: true
      placeholderText: root.hasClient ? "secret saved" : "client secret"
      foreground: root.foreground
      font.family: root.fontFamily
      Keys.onEscapePressed: { if (root.host) root.host.close() }
      Keys.onUpPressed: function(e) { root.navigate(0, -1); e.accepted = true }
      Keys.onDownPressed: function(e) { root.navigate(0, 1); e.accepted = true }
      onAccepted: root.save(true)
    }
    Text {
      visible: root.mode === "google"
      width: parent.width
      textFormat: Text.PlainText
      text: root.hasSession ? "Signed in to Google." : "Not signed in: press Sign in."
      color: root.hasSession ? root.foreground : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    // ---- Script -------------------------------------------------------------
    TextField {
      id: cmdField
      visible: root.mode === "command"
      width: parent.width
      placeholderText: "python3 /path/to/my_script.py --out {out}"
      foreground: root.foreground
      font.family: root.fontFamily
      Keys.onEscapePressed: { if (root.host) root.host.close() }
      Keys.onUpPressed: function(e) { root.navigate(0, -1); e.accepted = true }
      Keys.onDownPressed: function(e) { root.navigate(0, 1); e.accepted = true }
      onAccepted: root.save(false)
    }
    TextField {
      id: watchField
      visible: root.mode === "command"
      width: parent.width
      placeholderText: "file to watch for instant updates (optional)"
      foreground: root.foreground
      font.family: root.fontFamily
      Keys.onEscapePressed: { if (root.host) root.host.close() }
      Keys.onUpPressed: function(e) { root.navigate(0, -1); e.accepted = true }
      Keys.onDownPressed: function(e) { root.navigate(0, 1); e.accepted = true }
      onAccepted: root.save(false)
    }

    // ---- actions ------------------------------------------------------------
    Row {
      spacing: Style.space(8)

      Button {
        id: loginBtn
        visible: root.mode === "google"
        hasCursor: root.zoneName === "login"
        text: root.hasSession ? "Sign in again" : "Sign in"
        enabled: root.ready && !root.busy
        opacity: enabled ? 1 : 0.5
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.save(true)
      }

      Button {
        id: saveBtn
        hasCursor: root.zoneName === "save"
        text: "Save and connect"
        enabled: root.ready && !root.busy && !(root.mode === "google" && !root.hasSession)
        opacity: enabled ? 1 : 0.5
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.save(false)
      }
    }

    // Omarchy is built around coding agents, so the form has a way out to one:
    // the user's default agent opens with a prompt that sends it to AGENTS.md,
    // which says where the config lives and how to change it without ever
    // printing a credential.
    Row {
      spacing: Style.space(10)

      Button {
        id: agentBtn
        hasCursor: root.zoneName === "agent"
        text: "Set up with my agent"
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.askAgent()
      }
      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: "opens your default agent"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    // The key bound to this plugin right now (Hyprland's own table, read by the
    // widget), so the user can see which shortcut is theirs.
    Row {
      spacing: Style.space(4)

      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.shortcutKeys.length > 0 ? "Shortcut" : "Shortcut: none bound (see the README)"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        rightPadding: Style.space(6)
      }
      Repeater {
        model: root.shortcutKeys
        KeyCap { required property string modelData; label: modelData; foreground: root.foreground; fontFamily: root.fontFamily }
      }
    }

    Text {
      width: parent.width
      visible: text !== ""
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      text: root.status
      color: root.statusBad ? root.urgent : root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      width: parent.width
      visible: root.authUrl !== ""
      textFormat: Text.PlainText
      wrapMode: Text.WrapAnywhere
      maximumLineCount: 3
      elide: Text.ElideRight
      text: "If the browser did not open, open this link:\n" + root.authUrl
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
  }
}
