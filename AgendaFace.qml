import QtQuick
import qs.Commons

// One face of the bar label: title and value. The widget stacks two of these
// and rolls between them, so each is plain layout over a `face` record that
// Model.faces() already decided. Nothing here chooses what to say. The icon
// that leads the label belongs to the widget, not to a face: it stays put
// while the text rolls.
//
//   now    kickboxing 1h12     (remaining time bold)
//   next   shower en 25m       (wait time bold)
//   empty  sin bloques
Item {
  id: root

  property var face: null
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family
  property real maxWidth: Style.space(210)

  readonly property string kind: face ? String(face.kind) : "empty"
  readonly property real gap: Style.space(6)

  // Natural width, ignoring the cap: the widget sizes itself to the wider of
  // its two faces so hovering never moves the neighbours.
  readonly property real naturalWidth: titleText.implicitWidth
    + (valueText.text !== "" ? gap + valueText.implicitWidth : 0)

  implicitWidth: Math.min(maxWidth, naturalWidth)
  implicitHeight: titleText.implicitHeight

  Text {
    id: titleText
    textFormat: Text.PlainText
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    width: Math.max(0, Math.min(implicitWidth, root.width
                    - (valueText.text !== "" ? root.gap + valueText.implicitWidth : 0)))
    text: root.face ? String(root.face.title) : ""
    color: root.kind === "empty" ? Qt.darker(root.foreground, 1.55) : root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.body
    elide: Text.ElideRight
    renderType: Text.NativeRendering
  }

  Text {
    id: valueText
    textFormat: Text.PlainText
    anchors.left: titleText.right
    anchors.leftMargin: root.gap
    anchors.verticalCenter: parent.verticalCenter
    text: root.face ? String(root.face.value) : ""
    color: root.face && root.face.urgent ? root.urgent : root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.body
    font.bold: true
    renderType: Text.NativeRendering
  }
}
