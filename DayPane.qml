import QtQuick
import qs.Commons
import "Model.js" as Model

// One day on screen: its title, its whole-day chips and its timeline.
//
// The panel keeps two of these side by side so a day change can be a real
// carousel (one leaves while the next arrives) rather than a swap. A pane is
// told only which day to show; everything it draws is derived from that and
// the data, so the pane that is off screen is always a complete, ready day.
Item {
  id: root

  property var events: []
  property real nowMs: 0
  property real asOf: 0
  property real today: 0
  property int dayOffset: 0
  property bool loaded: false
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color track: Style.selectedFillFor(foreground, Color.accent)
  readonly property real viewDay: Model.addDays(today, dayOffset)
  readonly property bool viewingToday: dayOffset === 0
  readonly property var rows: Model.timeline(events, viewDay, nowMs)
  readonly property var allDay: Model.allDayForDay(events, viewDay)
  readonly property int eventRows: rows.filter(function(r) { return r.type === "event" }).length

  // The refresher reads seven days from midnight. A day past that horizon is
  // not empty, it is unread, and showing it blank would be a lie.
  readonly property real horizon: asOf > 0 ? Model.addDays(Model.dayStart(asOf), 7) : 0
  readonly property bool beyondData: asOf > 0 && viewDay >= horizon

  property string expandedId: ""

  // The keyboard cursor, by event id so it survives the rows being rebuilt
  // every minute. Empty until a key is used: a mouse-only user never sees it.
  property string cursorId: ""
  readonly property int cursorRow: {
    if (root.cursorId === "") return -1
    for (var i = 0; i < root.rows.length; i++)
      if (root.rows[i].type === "event" && root.rows[i].event.id === root.cursorId) return i
    return -1
  }

  // How far the list is pushed past an end it cannot scroll beyond: the nudge
  // that says "that is the edge".
  property real nudge: 0

  // Layout columns: times right-aligned in the gutter, the spine just right of
  // it, content after that.
  readonly property real gutterW: Style.space(40)
  readonly property real spineX: gutterW + Style.space(10)
  readonly property real contentX: spineX + Style.space(14)

  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  function collapse() { root.expandedId = "" }

  // Today opens on the running block, not at 05:00: the morning is already
  // gone and the answer is lower down. Other days open at the top.
  function scrollToFocus() {
    var target = -1
    if (root.viewingToday) {
      for (var i = 0; i < root.rows.length; i++) {
        // The time line is a "now" row, or, when now falls inside free time, that gap row carries it.
        if (root.rows[i].type === "now" || (root.rows[i].type === "gap" && root.rows[i].nowInside)) { target = i; break }
      }
      if (target < 0) {
        for (var j = 0; j < root.rows.length; j++) {
          if (root.rows[j].type === "event" && root.rows[j].live) { target = j; break }
        }
      }
    }
    // Land on a row boundary two rows above the target, never mid-row: a
    // half-clipped line at the top of the list reads as a rendering fault.
    var item = target >= 0 ? rowRepeater.itemAt(Math.max(0, target - 2)) : null
    var wanted = item ? item.y : 0
    flick.contentY = Math.max(0, Math.min(wanted, Math.max(0, flick.contentHeight - flick.height)))
  }

  readonly property real maxScroll: Math.max(0, flick.contentHeight - flick.height)

  // Every scroll goes through one animation, so a key, the wheel and the
  // cursor all move the list the same way: a quick start that settles gently,
  // the same curve the day change uses. Retargeting mid-flight restarts from
  // where the list is, so holding a key never queues up.
  property bool touched: false   // the person has scrolled since this day was shown

  function scrollTo(y) {
    root.touched = true
    var target = Math.max(0, Math.min(y, root.maxScroll))
    scrollAnim.stop()
    if (Math.abs(target - flick.contentY) < 1) return false
    scrollAnim.to = target
    scrollAnim.start()
    return true
  }

  function scrollBy(px) {
    var base = scrollAnim.running ? scrollAnim.to : flick.contentY
    if (!scrollTo(base + px)) bump(px > 0 ? 1 : -1)
  }

  // The edge: the list gives a few pixels and springs back.
  function bump(dir) {
    bumpAnim.stop()
    bumpOut.to = dir * -Style.space(9)
    bumpAnim.start()
  }

  function contentY() { return flick.contentY }
  function animating() { return scrollAnim.running || bumpAnim.running }

  function navRows() {
    var nav = []
    for (var i = 0; i < root.rows.length; i++) if (root.rows[i].type === "event") nav.push(i)
    return nav
  }

  // Bring the cursor row fully into view, with a little air above and below.
  function revealCursor() {
    var item = root.cursorRow >= 0 ? rowRepeater.itemAt(root.cursorRow) : null
    if (!item) return
    var air = Style.space(14)
    if (item.y < flick.contentY + air) scrollTo(item.y - air)
    else if (item.y + item.height > flick.contentY + flick.height - air) scrollTo(item.y + item.height - flick.height + air)
  }

  // One step through the blocks. The first press lands on the running block (or
  // the next one up), because that is where the eye already is; after that each
  // press moves one block. Past either end it nudges instead of doing nothing.
  function moveCursor(dir) {
    var nav = navRows()
    if (nav.length === 0) { bump(dir); return }
    var pos = nav.indexOf(root.cursorRow)
    if (pos < 0) {
      var start = -1
      for (var i = 0; i < nav.length; i++) {
        var r = root.rows[nav[i]]
        if (r.live === true || (start < 0 && r.upNext === true)) { start = nav[i]; if (r.live === true) break }
      }
      var land = start >= 0 ? start : (dir > 0 ? nav[0] : nav[nav.length - 1])
      root.cursorId = root.rows[land].event.id
      Qt.callLater(revealCursor)
      return
    }
    var next = pos + dir
    if (next < 0 || next >= nav.length) { bump(dir); return }
    root.cursorId = root.rows[nav[next]].event.id
    Qt.callLater(revealCursor)
  }

  function toggleCursor() {
    if (root.cursorRow < 0) return
    var ev = root.rows[root.cursorRow].event
    if (ev.location === "" && ev.description === "") { bump(1); return }
    root.expandedId = root.expandedId === ev.id ? "" : ev.id
    // The row grows while it animates: follow it down once it has.
    revealTimer.restart()
  }

  Timer { id: revealTimer; interval: 190; onTriggered: root.revealCursor() }

  NumberAnimation {
    id: scrollAnim
    target: flick
    property: "contentY"
    duration: 260
    easing.type: Easing.BezierSpline
    easing.bezierCurve: [0.05, 0.7, 0.1, 1.0, 1.0, 1.0]
  }

  SequentialAnimation {
    id: bumpAnim
    NumberAnimation { id: bumpOut; target: root; property: "nudge"; duration: 80; easing.type: Easing.OutQuad }
    NumberAnimation { target: root; property: "nudge"; to: 0; duration: 340; easing.type: Easing.OutBack; easing.overshoot: 2.2 }
  }

  // Bring this pane to a given day, from scratch. Called every time the pane is
  // about to be shown, and not left to a dayOffset change: the pane that was
  // hidden may already hold that very day (so nothing changes and nothing
  // fires), with whatever scroll position and cursor it was left at. Coming
  // back to today must land on what is happening now, not where it was parked.
  function prepare(day) {
    root.dayOffset = day
    collapse()
    root.cursorId = ""
    root.touched = false
    scrollAnim.stop()
    bumpAnim.stop()
    root.nudge = 0
    scrollToFocus()
    // Rows are rebuilt and laid out just after the day is set; once that has
    // happened, aim again, since the first pass can read a half-built list.
    focusTimer.restart()
  }

  Timer { id: focusTimer; interval: 40; onTriggered: if (root.cursorId === "" && !scrollAnim.running) root.scrollToFocus() }

  Column {
    id: column
    anchors.fill: parent
    spacing: Style.space(8)

    Text {
      id: title
      width: parent.width
      textFormat: Text.PlainText
      text: Model.dayTitle(root.viewDay, root.nowMs)
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      font.letterSpacing: 1.2
      font.capitalization: Font.AllUppercase
    }

    // Whole-day items (birthdays, deadlines) have no hour to sit at, so they
    // are chips above the timeline instead of rows in it.
    Flow {
      id: chips
      visible: root.allDay.length > 0
      width: parent.width
      spacing: Style.space(4)

      Repeater {
        model: root.allDay

        Rectangle {
          required property var modelData
          implicitWidth: dayText.implicitWidth + Style.space(12)
          implicitHeight: dayText.implicitHeight + Style.space(6)
          color: "transparent"
          border.width: Math.max(1, Style.space(1))
          border.color: root.alpha(root.foreground, 0.35)

          Text {
            id: dayText
            anchors.centerIn: parent
            width: Math.min(implicitWidth, root.width - Style.space(24))
            textFormat: Text.PlainText
            elide: Text.ElideRight
            text: modelData.title
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }
      }
    }

    Item {
      id: listBox
      width: parent.width
      // The pane's height is fixed by the panel, so chips take their room from
      // the list instead of growing the card.
      height: Math.max(0, root.height - title.height - column.spacing
                       - (chips.visible ? chips.height + column.spacing : 0))

      Flickable {
        id: flick
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: rowsColumn.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        // The wheel is handled below, so it animates like everything else
        // instead of jumping in fixed pixel steps.
        interactive: false

        WheelHandler {
          acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
          onWheel: function(event) { root.scrollBy(-event.angleDelta.y / 120 * Style.space(64)) }
        }

        // The spine the times hang from. Blocks sit on it as nodes; the running
        // block is a solid segment of it, so "where am I in the day" is the one
        // thing that is not a line of text.
        Rectangle {
          x: root.spineX - width / 2
          width: Math.max(1, Style.space(1))
          height: rowsColumn.height
          color: root.alpha(root.foreground, 0.22)
        }

        Column {
          id: rowsColumn
          width: flick.width
          spacing: Style.space(2)
          transform: Translate { y: root.nudge }

          Repeater {
            id: rowRepeater
            model: root.rows

            Item {
              id: row
              required property var modelData
              required property int index
              readonly property bool hasCursor: root.cursorRow === index
              readonly property string kind: modelData.type
              readonly property bool isEvent: kind === "event"
              readonly property bool isGap: kind === "gap"
              readonly property bool isNow: kind === "now"
              readonly property var ev: isEvent ? modelData.event : null
              readonly property bool live: isEvent && modelData.live === true
              readonly property bool past: isEvent && modelData.past === true
              readonly property bool upNext: isEvent && modelData.upNext === true
              readonly property bool expanded: isEvent && root.expandedId === ev.id
              readonly property bool hasDetail: isEvent && (ev.location !== "" || ev.description !== "")
              readonly property real headH: live ? Style.space(60) : (isEvent ? Style.space(32) : Style.space(22))
              readonly property real centerY: headH / 2

              width: rowsColumn.width
              height: headH + (expanded ? detail.implicitHeight + Style.space(8) : 0)
              opacity: past ? 0.45 : 1

              Behavior on height { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }

              // The keyboard cursor: an outline around the row it is on. It takes
              // the exact box of what it surrounds: an ordinary row is outlined
              // from just left of its title to the edge (the same box as its hover
              // wash), and the running block is outlined as its own card, so the
              // two borders coincide instead of one floating around the other.
              Rectangle {
                z: 2
                x: row.live ? root.contentX : root.contentX - Style.space(6)
                width: row.live ? row.width - root.contentX - Style.space(2) : row.width - x
                height: row.headH
                color: "transparent"
                border.width: Math.max(1, Style.space(1))
                border.color: root.alpha(root.foreground, 0.55)
                opacity: row.hasCursor ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 140 } }
              }

              // hover wash behind the whole row
              Rectangle {
                anchors.left: parent.left
                anchors.leftMargin: root.contentX - Style.space(6)
                anchors.right: parent.right
                height: parent.height
                visible: row.isEvent && !row.live
                color: rowMouse.containsMouse ? root.alpha(root.foreground, 0.07) : "transparent"
                Behavior on color { ColorAnimation { duration: 120 } }
              }

              // gutter: the time this row starts, right-aligned against the
              // spine; the now row and a gap holding "now" show the current
              // time in bold, like the clock face on the reference
              Text {
                x: 0
                width: root.gutterW
                y: row.centerY - height / 2
                horizontalAlignment: Text.AlignRight
                textFormat: Text.PlainText
                visible: row.isEvent || row.isNow || (row.isGap && modelData.nowInside === true)
                text: row.isEvent ? Model.clock(modelData.from) : Model.clock(root.nowMs)
                color: row.isNow || row.isGap ? root.foreground : (row.live ? root.foreground : root.dim)
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                font.bold: row.isNow || row.isGap || row.live
              }

              // node on the spine
              Rectangle {
                visible: !row.live && (row.isEvent || row.isNow || (row.isGap && modelData.nowInside === true))
                width: row.isEvent ? Style.space(6) : Style.space(8)
                height: width
                x: root.spineX - width / 2
                y: row.centerY - height / 2
                color: row.isEvent && !row.past ? Color.background : root.foreground
                border.width: row.isEvent && !row.past ? Math.max(1, Style.space(1)) : 0
                border.color: root.foreground
              }

              // the running block: a solid segment of the spine
              Rectangle {
                visible: row.live
                x: root.spineX - width / 2
                width: Math.max(3, Style.space(4))
                height: row.headH
                color: root.foreground
              }

              // ---- an ordinary block ------------------------------------------
              Text {
                id: lengthText
                visible: row.isEvent && !row.live
                anchors.right: parent.right
                anchors.rightMargin: Style.space(10)
                y: row.centerY - height / 2
                textFormat: Text.PlainText
                text: !row.isEvent ? "" : (row.upNext ? "in " + Model.span(row.ev.start - root.nowMs)
                                                      : Model.span(modelData.to - modelData.from))
                color: row.upNext ? root.foreground : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                font.bold: row.upNext
              }
              Text {
                visible: row.isEvent && !row.live
                x: root.contentX
                width: Math.max(0, lengthText.x - Style.space(8) - root.contentX)
                y: row.centerY - height / 2
                textFormat: Text.PlainText
                elide: Text.ElideRight
                text: row.isEvent ? row.ev.title : ""
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: row.upNext
              }

              // ---- the running block: a card that fills as it elapses --------
              Rectangle {
                id: card
                visible: row.live
                x: root.contentX
                width: row.width - root.contentX - Style.space(2)
                height: row.headH
                color: root.alpha(root.foreground, 0.07)
                border.width: Math.max(1, Style.space(1))
                border.color: root.alpha(root.foreground, 0.4)

                // Elapsed time fills the card from the left, the way the bar under
                // the title does, and stops exactly inside the border: inset by the
                // border's width on every side, so no sliver of the card shows
                // around it, and a thin brighter edge marks where "now" is.
                Item {
                  id: fillBox
                  readonly property real inset: Math.max(1, Style.space(1))
                  x: inset
                  y: inset
                  width: parent.width - inset * 2
                  height: parent.height - inset * 2
                  clip: true

                  Rectangle {
                    width: fillBox.width * (row.live ? modelData.progress : 0)
                    height: parent.height
                    color: root.alpha(root.foreground, 0.2)
                    Behavior on width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }

                    Rectangle {
                      visible: parent.width > 1 && parent.width < fillBox.width - 1
                      anchors.right: parent.right
                      width: Math.max(1, Style.space(1))
                      height: parent.height
                      color: root.alpha(root.foreground, 0.55)
                    }
                  }
                }

                Column {
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: Style.space(12)
                  anchors.rightMargin: Style.space(12)
                  spacing: Style.space(3)

                  Text {
                    width: parent.width
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    text: row.live ? row.ev.title : ""
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                  }
                  Text {
                    width: parent.width
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    text: row.live ? Model.span(row.ev.end - root.nowMs) + " left · until " + Model.clock(row.ev.end) : ""
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }
                }
              }

              // ---- free time ----------------------------------------------------
              Row {
                visible: row.isGap
                x: root.contentX
                width: row.width - root.contentX - Style.space(10)
                y: row.centerY - height / 2
                spacing: Style.space(8)

                Text {
                  id: gapLabel
                  textFormat: Text.PlainText
                  text: row.isGap ? "free · " + Model.span(modelData.to - modelData.from) : ""
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
                Rectangle {
                  visible: row.isGap && modelData.nowInside === true
                  anchors.verticalCenter: parent.verticalCenter
                  width: parent.width - gapLabel.implicitWidth - parent.spacing
                  height: Math.max(1, Style.space(1))
                  color: root.foreground
                }
              }

              // ---- the current time, when nothing is running ----------------------
              Row {
                visible: row.isNow
                x: root.contentX
                width: row.width - root.contentX - Style.space(10)
                y: row.centerY - height / 2
                spacing: Style.space(8)

                Text {
                  id: nowLabel
                  textFormat: Text.PlainText
                  text: "now"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                Rectangle {
                  anchors.verticalCenter: parent.verticalCenter
                  width: parent.width - nowLabel.implicitWidth - parent.spacing
                  height: Math.max(1, Style.space(1))
                  color: root.foreground
                }
              }

              // ---- detail, on click -----------------------------------------------
              Text {
                id: detail
                visible: row.expanded
                x: root.contentX
                y: row.headH + Style.space(4)
                width: row.width - root.contentX - Style.space(10)
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                maximumLineCount: 5
                elide: Text.ElideRight
                text: row.isEvent ? (row.ev.location !== "" ? row.ev.location + "\n" : "") + row.ev.description : ""
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              MouseArea {
                id: rowMouse
                anchors.left: parent.left
                anchors.leftMargin: root.contentX
                anchors.right: parent.right
                height: row.headH
                hoverEnabled: true
                enabled: row.hasDetail
                cursorShape: row.hasDetail ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: root.expandedId = row.expanded ? "" : row.ev.id
              }
            }
          }
        }
      }

      // The list runs on past an edge: fade that edge out, so "there is more"
      // is something you see and not something you discover.
      Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: Style.space(16)
        opacity: flick.contentY > 2 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 160 } }
        gradient: Gradient {
          GradientStop { position: 0.0; color: Color.popups.background }
          GradientStop { position: 1.0; color: Qt.rgba(Color.popups.background.r, Color.popups.background.g, Color.popups.background.b, 0) }
        }
      }
      Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: Style.space(16)
        opacity: flick.contentY < root.maxScroll - 2 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 160 } }
        gradient: Gradient {
          GradientStop { position: 0.0; color: Qt.rgba(Color.popups.background.r, Color.popups.background.g, Color.popups.background.b, 0) }
          GradientStop { position: 1.0; color: Color.popups.background }
        }
      }

      Text {
        visible: root.eventRows === 0 && root.loaded
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: root.beyondData ? "not read yet" : (root.allDay.length > 0 ? "no timed blocks" : "nothing on the calendar")
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }
    }
  }
}
