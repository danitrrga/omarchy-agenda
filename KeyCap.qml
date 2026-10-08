import QtQuick
import qs.Commons

// A key as it is printed on a keyboard: a small outlined box around its name.
// Modifier and special keys are drawn as their symbol, the way a keycap shows
// them, because "Super Shift C" is three words of text where a glance at
// "▦ ⇧ C" is enough. Everything else is shown as typed.
//
// Super has no standard character, so it is drawn: four small squares, the
// shape every Super key carries and in keeping with the shell's square corners.
Rectangle {
  id: root

  property string label: ""
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  // Characters all present in the JetBrainsMono Nerd Font the shell already
  // uses (checked with fc-list), so no extra font is involved.
  readonly property var symbols: ({ "Shift": "⇧", "Ctrl": "⌃", "Alt": "⌥", "Enter": "⏎" })
  readonly property bool isSuper: label === "Super"
  readonly property string shown: symbols[label] !== undefined ? symbols[label] : label

  readonly property real cell: Math.max(2, Style.space(3))
  readonly property real gap: Math.max(1, Style.space(1))
  readonly property real contentW: isSuper ? cell * 2 + gap : text.implicitWidth
  readonly property real contentH: isSuper ? cell * 2 + gap : text.implicitHeight

  implicitHeight: text.implicitHeight + Style.space(4)
  implicitWidth: Math.max(implicitHeight, contentW + Style.space(8))
  color: "transparent"
  border.width: Math.max(1, Style.space(1))
  border.color: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.4)

  Text {
    id: text
    visible: !root.isSuper
    anchors.centerIn: parent
    textFormat: Text.PlainText
    text: root.isSuper ? " " : root.shown
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    font.bold: true
  }

  Grid {
    visible: root.isSuper
    anchors.centerIn: parent
    columns: 2
    spacing: root.gap

    Repeater {
      model: 4
      Rectangle { width: root.cell; height: root.cell; color: root.foreground }
    }
  }
}
