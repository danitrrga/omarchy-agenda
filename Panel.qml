import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Model.js" as Model

// The agenda panel: what the bar icon opens.
//
// Three layers, ordered by how often they are the answer:
//   1. the hero: the running block (or the next one) and how long is left;
//      always about NOW, whatever day is being browsed below it
//   2. the day strip and the timeline of the chosen day
//   3. the footer: how fresh the reading is
//
// Data comes from the host widget, never a second read: two readers of one
// file disagree for a tick, and a panel that disagrees with its own bar label
// is worse than either being slightly stale. Read-only: nothing here writes.
Panel {
  id: root
  moduleName: "io.github.danitrrga.agenda"
  ipcTarget: ""
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null

  readonly property var events: hostWidget ? hostWidget.events : []
  readonly property real nowMs: hostWidget ? hostWidget.nowMs : Date.now()
  readonly property var face: hostWidget ? hostWidget.face : null
  readonly property real asOf: hostWidget ? hostWidget.asOf : 0
  readonly property int failures: hostWidget ? hostWidget.failures : 0
  readonly property bool loaded: hostWidget ? hostWidget.loaded : false
  readonly property bool configured: hostWidget ? hostWidget.configured : true
  readonly property var config: hostWidget ? hostWidget.config : ({})
  readonly property var shortcutKeys: hostWidget ? hostWidget.shortcutKeys : []

  // Settings replace the day in the same space. With no calendar connected the
  // panel opens straight on them: there is nothing else worth showing.
  property bool settingsOpen: false
  readonly property bool stale: hostWidget ? hostWidget.stale : false
  readonly property bool refreshing: hostWidget ? hostWidget.refreshing : false
  readonly property string lastError: hostWidget ? hostWidget.lastError : ""

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color track: Style.selectedFillFor(foreground, Color.accent)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // The day on screen, as an offset from today so it rolls over at midnight
  // with the panel open instead of staying pinned to yesterday.
  property int dayOffset: 0
  readonly property int dayCount: 7
  readonly property real today: Model.dayStart(nowMs)
  readonly property real viewDay: Model.addDays(today, dayOffset)
  readonly property bool viewingToday: dayOffset === 0


  readonly property real contentW: Style.space(380)
  // Fixed on purpose: a panel whose size changes after it maps opens in the
  // wrong place, and the row count differs every day.
  readonly property real paneH: Style.space(290)

  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  // Two panes side by side: the one on screen and the one being brought in.
  property bool frontIsA: true
  readonly property var frontPane: frontIsA ? paneA : paneB
  readonly property var backPane: frontIsA ? paneB : paneA

  // What the arrow keys and Enter do, in one place so a scripted check drives
  // exactly the code a key press does.
  function keyMove(dx, dy) {
    if (root.settingsOpen) settingsView.navigate(dx, dy)
    else if (dx !== 0) root.shiftDay(dx)
    else root.frontPane.moveCursor(dy)
  }

  function keyActivate() {
    if (root.settingsOpen) settingsView.activate()
    else root.frontPane.toggleCursor()
  }

  // Give the keyboard back to the panel (a settings field had it).
  function takeKeys() { keyCatcher.forceActiveFocus() }

  function setSettings(on) {
    root.settingsOpen = on
    if (on) Qt.callLater(function() { settingsView.focusFirst() })
    else Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function open() {
    root.setSettings(!root.configured)
    // Opening is the moment the reading must be current. If it is already
    // recent the refresh is skipped, so mashing the key does not hammer Google.
    if (hostWidget && (nowMs - asOf > 2 * 60000)) hostWidget.refresh()
    root.snapToDay(0)
    root.controller.show()
    Qt.callLater(function() { root.frontPane.scrollToFocus() })
  }

  function close() { root.controller.hide() }
  function toggle() { if (root.opened) root.close(); else root.open() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.hostWidget || root, direction)
    return false
  }

  // No animation: used when the panel opens, so it never opens mid-slide.
  function snapToDay(n) {
    if (slide.running) slide.complete()
    root.dayOffset = n
    root.frontPane.prepare(n)
    root.frontPane.x = 0
    root.frontPane.opacity = 1
    root.frontPane.visible = true
    root.backPane.visible = false
  }

  // Moving forward in time sends the current day out to the left and brings
  // the next in from the right; going back is the mirror. A second key press
  // mid-slide finishes the first at once, so holding an arrow stays snappy
  // instead of queueing.
  function goToDay(n) {
    var target = Math.min(root.dayCount - 1, Math.max(0, n))
    if (target === root.dayOffset) return
    if (slide.running) slide.complete()
    var dir = target > root.dayOffset ? 1 : -1
    var incoming = root.backPane
    var outgoing = root.frontPane
    root.dayOffset = target
    incoming.prepare(target)
    incoming.x = dir * pager.width
    incoming.opacity = 0
    incoming.visible = true
    outX.target = outgoing; outX.to = -dir * pager.width
    inX.target = incoming; inX.from = dir * pager.width
    outFade.target = outgoing
    inFade.target = incoming
    slide.start()
  }

  function shiftDay(delta) { root.goToDay(root.dayOffset + delta) }

  // for scripted checks of the keyboard path
  function debug() {
    var pane = root.frontPane
    return JSON.stringify({ y: pane.contentY(), max: pane.maxScroll, cursorId: pane.cursorId, cursorRow: pane.cursorRow,
                            nudge: pane.nudge, animating: pane.animating(), expanded: pane.expandedId,
                            settings: root.settingsOpen, mode: settingsView.mode, zone: settingsView.zone, zoneName: settingsView.zoneName,
                            fieldFocus: settingsView.fieldFocused })
  }

  function stamp(ms) { return ms > 0 ? Model.clock(ms) : "—" }

  readonly property string footer: {
    if (!root.configured) return "no calendar connected"
    if (!root.loaded) return root.lastError !== "" ? "could not read the calendar · " + root.lastError : "reading the calendar…"
    if (root.failures > 0) return root.failures + (root.failures === 1 ? " calendar could not be read" : " calendars could not be read")
    if (root.stale) return "not synced since " + Model.stampSince(root.asOf, root.nowMs) + (root.lastError !== "" ? " · " + root.lastError : "")
    return "synced " + root.stamp(root.asOf)
  }
  readonly property bool footerBad: root.loaded ? (root.failures > 0 || root.stale) : root.lastError !== ""

  Connections {
    target: root.hostWidget
    // A background refresh must not yank the list back to "now" while someone is reading it.
    function onEventsChanged() { if (root.opened && !root.frontPane.touched) Qt.callLater(function() { root.frontPane.scrollToFocus() }) }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    // Fixed height on purpose: a panel whose size changes after it maps opens
    // in the wrong place, and the day's row count differs every day.
    contentWidth: panel.fittedContentWidth(root.contentW)
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // While a settings field has the cursor every key belongs to it.
      blocked: root.settingsOpen && settingsView.fieldFocused
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        root.keyMove(dx, dy)
      }
      onActivateRequested: root.keyActivate()
      onTextKey: function(t) {
        if (t === "r" && root.hostWidget) root.hostWidget.refresh()
        else if (t === "t") root.goToDay(0)
        else if (t === "s") root.setSettings(!root.settingsOpen)
      }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(10)

        // The day, or the settings in its place: same footprint, so switching
        // never resizes the card.
        Item {
          id: stage
          width: parent.width
          height: mainColumn.implicitHeight

          Settings {
            id: settingsView
            anchors.fill: parent
            visible: root.settingsOpen
            host: root
          }

          Column {
            id: mainColumn
            width: parent.width
            spacing: Style.space(10)
            visible: !root.settingsOpen

            // ---- 1. now ---------------------------------------------------------
            // The hero is laid out here rather than with PanelHero: that one reserves
            // a leading icon column, and this panel carries no icons.
            Item {
              id: hero
              width: parent.width
              height: Math.max(heroLabels.implicitHeight, pill.implicitHeight)

              readonly property string heroDetail: {
                if (!root.face || !root.face.event) return ""
                var e = root.face.event
                return root.face.state === "now"
                  ? Model.span(e.end - root.nowMs) + " left"
                  : "in " + Model.span(e.start - root.nowMs)
              }

              Column {
                id: heroLabels
                anchors.left: parent.left
                anchors.right: pill.visible ? pill.left : parent.right
                anchors.rightMargin: pill.visible ? Style.space(10) : 0
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  text: root.face && root.face.event ? root.face.event.title : (root.configured ? "Nothing pending" : "No calendar")
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                  font.bold: true
                }
                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  visible: text !== ""
                  text: {
                    if (!root.face || !root.face.event) return !root.configured ? "NOT SET UP" : (root.loaded ? "CALENDAR FREE" : "")
                    var e = root.face.event
                    if (root.face.state === "now") return ("Now · " + Model.clock(e.start) + " – " + Model.clock(e.end)).toUpperCase()
                    return ("Next · " + Model.dayWord(Model.dayStart(e.start), root.nowMs) + " " + Model.clock(e.start)).toUpperCase()
                  }
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  font.letterSpacing: 1.2
                }
              }

              Rectangle {
                id: pill
                visible: hero.heroDetail !== ""
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                implicitWidth: pillText.implicitWidth + Style.space(10)
                implicitHeight: pillText.implicitHeight + Style.space(4)
                width: implicitWidth
                height: implicitHeight
                color: "transparent"
                border.width: Math.max(1, Style.space(1))
                border.color: root.alpha(root.foreground, 0.4)

                Text {
                  id: pillText
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: hero.heroDetail
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
              }
            }

            // Elapsed share of the running block; absent when nothing is running,
            // because an empty track would read as "0% done" of something.
            Rectangle {
              visible: root.face && root.face.progress >= 0
              width: parent.width
              height: Math.max(2, Style.space(3))
              color: root.track

              Rectangle {
                height: parent.height
                width: parent.width * (root.face ? Math.max(0, root.face.progress) : 0)
                color: root.foreground
              }
            }

            // What is after it, so "next" is on screen without hovering anything.
            Text {
              visible: !!root.face && !!root.face.rest
              width: parent.width
              textFormat: Text.PlainText
              elide: Text.ElideRight
              text: root.face && root.face.rest ? "Then  " + root.face.rest.title + "  ·  " + root.face.rest.value : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            PanelSeparator { }

            // ---- 2. the days ----------------------------------------------------
            Row {
              id: strip
              width: parent.width
              spacing: Style.space(4)

              Repeater {
                model: root.dayCount

                Rectangle {
                  id: chip
                  required property int index
                  readonly property real dayMs: Model.addDays(root.today, index)
                  readonly property bool selected: index === root.dayOffset
                  readonly property real busy: Model.busyShare(root.configured ? root.events : [], dayMs)
                  width: (strip.width - strip.spacing * (root.dayCount - 1)) / root.dayCount
                  height: Style.space(46)
                  color: selected ? root.track : (chipMouse.containsMouse ? root.alpha(root.foreground, 0.08) : "transparent")
                  border.width: selected ? 0 : Math.max(1, Style.space(1))
                  border.color: root.alpha(root.foreground, 0.18)

                  Column {
                    anchors.centerIn: parent
                    spacing: 0
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter
                      textFormat: Text.PlainText
                      text: index === 0 ? "today" : Model.weekdayShort(chip.dayMs)
                      color: chip.selected ? root.foreground : root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter
                      textFormat: Text.PlainText
                      text: Model.dayNumber(chip.dayMs)
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.subtitle
                      font.bold: chip.selected
                    }
                  }

                  // How full the day is: one bar, merged so parallel lectures do not
                  // count twice. A glance at the strip says which day to avoid.
                  Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: Style.space(6)
                    anchors.rightMargin: Style.space(6)
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: Style.space(5)
                    height: Math.max(2, Style.space(2))
                    color: root.alpha(root.foreground, 0.16)

                    Rectangle {
                      height: parent.height
                      width: parent.width * chip.busy
                      color: root.foreground
                      opacity: chip.selected ? 1 : 0.7
                    }
                  }

                  // Today's mark survives selecting another day, so "where am I" is
                  // never lost while browsing.
                  Rectangle {
                    visible: index === 0
                    anchors.top: parent.top
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width * 0.5
                    height: Math.max(1, Style.space(2))
                    color: root.foreground
                  }

                  MouseArea {
                    id: chipMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.goToDay(chip.index)
                  }
                }
              }
            }

            // ---- the carousel ---------------------------------------------------
            Item {
              id: pager
              width: parent.width
              height: root.paneH
              clip: true

              DayPane {
                id: paneA
                width: pager.width
                height: pager.height
                events: root.configured ? root.events : []
                nowMs: root.nowMs
                asOf: root.asOf
                today: root.today
                loaded: root.loaded && root.configured
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              DayPane {
                id: paneB
                visible: false
                width: pager.width
                height: pager.height
                events: root.configured ? root.events : []
                nowMs: root.nowMs
                asOf: root.asOf
                today: root.today
                loaded: root.loaded && root.configured
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              // A decelerating curve: it leaves fast and settles gently, which
              // reads as snappy yet smooth. The control points are the Material
              // "emphasized decelerate" curve. Positions slide the whole way; the
              // fade only softens the edges and finishes before the slide does.
              ParallelAnimation {
                id: slide
                onFinished: {
                  root.frontPane.visible = false
                  root.frontIsA = !root.frontIsA
                  root.frontPane.x = 0
                  root.frontPane.opacity = 1
                }

                NumberAnimation {
                  id: outX
                  property: "x"
                  from: 0
                  duration: 300
                  easing.type: Easing.BezierSpline
                  easing.bezierCurve: [0.05, 0.7, 0.1, 1.0, 1.0, 1.0]
                }
                NumberAnimation {
                  id: inX
                  property: "x"
                  to: 0
                  duration: 300
                  easing.type: Easing.BezierSpline
                  easing.bezierCurve: [0.05, 0.7, 0.1, 1.0, 1.0, 1.0]
                }
                NumberAnimation { id: outFade; property: "opacity"; from: 1; to: 0; duration: 200; easing.type: Easing.OutQuad }
                NumberAnimation { id: inFade; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutQuad }
              }
            }

          }
        }

        PanelSeparator { }

        // ---- 3. the footer --------------------------------------------------
        // Freshness on the left; on the right only what is not obvious: that the
        // arrows change the day, R refreshes and S opens settings. Esc, the global
        // shortcut and the rest are left for the user to find (the shortcut is
        // shown in settings).
        Item {
          width: parent.width
          height: Math.max(footerText.implicitHeight, hints.implicitHeight)

          Text {
            id: footerText
            anchors.left: parent.left
            anchors.right: hints.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            elide: Text.ElideRight
            text: root.refreshing ? "updating…" : root.footer
            color: root.footerBad ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Row {
            id: hints
            visible: !root.settingsOpen
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(10)

            KeyCap { label: "← →"; foreground: root.foreground; fontFamily: root.fontFamily }

            // R and the word beside it are one button, written once.
            Item {
              width: refreshHint.implicitWidth
              height: refreshHint.implicitHeight
              opacity: refreshMouse.containsMouse ? 1 : 0.85

              Row {
                id: refreshHint
                spacing: Style.space(4)

                KeyCap { label: "r"; foreground: root.foreground; fontFamily: root.fontFamily }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  textFormat: Text.PlainText
                  text: "refresh"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              MouseArea {
                id: refreshMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: { if (root.hostWidget) root.hostWidget.refresh() }
              }
            }

            // S and the word beside it are one button, written once.
            Item {
              width: settingsHint.implicitWidth
              height: settingsHint.implicitHeight
              opacity: settingsMouse.containsMouse ? 1 : 0.85

              Row {
                id: settingsHint
                spacing: Style.space(4)

                KeyCap { label: "s"; foreground: root.foreground; fontFamily: root.fontFamily }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  textFormat: Text.PlainText
                  text: "settings"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              MouseArea {
                id: settingsMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.setSettings(true)
              }
            }
          }
        }
      }
    }
  }
}
