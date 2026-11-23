import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Right-click: pin a name to the workspace, rename its project everywhere
// (an alias), or go back to auto. Never opens empty: the auto name is
// pre-filled and selected, with one-click suggestions from what's there.
KeyboardPanel {
  id: panel

  property var widget: null
  property var info: null
  property int wsLabel: 0
  property var colors: null

  readonly property color ink: Commons.Color.popups.text
  readonly property color surface: {
    var c = Commons.Color.popups.background
    return Qt.rgba(c.r, c.g, c.b, 1)
  }
  readonly property color dim: colors ? colors.legible(ink, surface, 0.62, 4.5) : Util.alpha(ink, 0.65)
  readonly property string mono: widget && widget.bar ? widget.bar.fontFamily : Style.font.family
  readonly property string repo: info && info.aliasKey ? (info.aliasRepo || "") : ""
  readonly property bool aliased: info !== null && !!info.aliased

  contentWidth: panel.fittedContentWidth(Style.space(380))
  contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)
  focusTarget: panel.loadedContent ? panel.loadedContent.field : null

  onOpenChanged: if (open && panel.loadedContent) panel.loadedContent.reset()
  onInfoChanged: if (open && panel.loadedContent) panel.loadedContent.reset()

  function act(what) {
    var name = panel.loadedContent ? String(panel.loadedContent.field.text).trim() : ""
    if ((what === "pin" || what === "alias") && name === "") return
    if (panel.widget) {
      panel.widget.command(what, name)
      panel.widget.close()
    }
  }

  component ActionButton: Rectangle {
    id: btn
    property string label: ""
    property bool primary: false
    property bool enabledState: true
    signal activated()
    implicitWidth: btnText.implicitWidth + Style.space(20)
    implicitHeight: btnText.implicitHeight + Style.space(12)
    radius: Style.cornerRadius
    opacity: enabledState ? 1 : 0.4
    color: primary ? Util.alpha(panel.ink, btnMouse.containsMouse ? 0.22 : 0.16) : (btnMouse.containsMouse ? Util.alpha(panel.ink, 0.08) : "transparent")
    border.width: 1
    border.color: Util.alpha(panel.ink, primary ? 0.45 : 0.25)
    Accessible.role: Accessible.Button
    Accessible.name: btn.label
    Accessible.onPressAction: if (btn.enabledState) btn.activated()
    Text {
      id: btnText
      anchors.centerIn: parent
      text: btn.label
      textFormat: Text.PlainText
      color: panel.ink
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
      font.bold: btn.primary
    }
    MouseArea {
      id: btnMouse
      anchors.fill: parent
      hoverEnabled: true
      enabled: btn.enabledState
      cursorShape: Qt.PointingHandCursor
      onClicked: btn.activated()
    }
  }

  content: Item {
    id: body
    property alias field: field
    implicitHeight: column.implicitHeight

    function reset() {
      var i = panel.info
      field.text = i ? (i.pinned ? i.full : (i.autoName || i.full || "")) : ""
      field.selectAll()
      field.forceActiveFocus()
    }
    Component.onCompleted: reset()

    Column {
      id: column
      width: parent.width
      spacing: Style.space(10)

      Text {
        text: "Name workspace " + panel.wsLabel
        textFormat: Text.PlainText
        color: panel.ink
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }

      TextField {
        id: field
        width: parent.width
        font.family: panel.mono
        placeholderText: "inbox, music, client work…"
        maximumLength: 64
        onAccepted: panel.act("pin")
        Keys.onEscapePressed: panel.close()
      }

      Flow {
        width: parent.width
        spacing: Style.space(5)
        visible: panel.info !== null && panel.info.suggestions && panel.info.suggestions.length > 0

        Text {
          text: "SUGGESTIONS"
          textFormat: Text.PlainText
          color: panel.dim
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          font.bold: true
          height: Style.font.bodySmall + Style.space(10)
          verticalAlignment: Text.AlignVCenter
        }
        Repeater {
          model: panel.info && panel.info.suggestions ? panel.info.suggestions : []
          Rectangle {
            id: chip
            required property var modelData
            implicitWidth: chipRow.implicitWidth + Style.space(16)
            implicitHeight: chipRow.implicitHeight + Style.space(8)
            radius: height / 2
            color: chipMouse.containsMouse ? Util.alpha(panel.ink, 0.12) : Util.alpha(panel.ink, 0.05)
            border.width: 1
            border.color: Util.alpha(panel.ink, 0.2)
            Accessible.role: Accessible.Button
            Accessible.name: chip.modelData.text
            Accessible.onPressAction: { field.text = chip.modelData.text; field.forceActiveFocus() }
            Row {
              id: chipRow
              anchors.centerIn: parent
              spacing: Style.space(4)
              Text { text: chip.modelData.text; textFormat: Text.PlainText; color: panel.ink; font.family: panel.mono; font.pixelSize: Style.font.bodySmall }
              Text { text: chip.modelData.why; textFormat: Text.PlainText; color: panel.dim; font.family: Style.font.family; font.pixelSize: Style.font.caption; anchors.verticalCenter: parent.verticalCenter }
            }
            MouseArea {
              id: chipMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: { field.text = chip.modelData.text; field.forceActiveFocus() }
              onDoubleClicked: { field.text = chip.modelData.text; panel.act("pin") }
            }
          }
        }
      }

      Flow {
        width: parent.width
        spacing: Style.space(6)
        ActionButton { label: "Pin"; primary: true; onActivated: panel.act("pin") }
        ActionButton {
          label: panel.repo !== "" ? "Rename project “" + panel.repo + "”" : "Rename project"
          enabledState: panel.repo !== ""
          onActivated: panel.act("alias")
        }
        ActionButton {
          visible: panel.aliased
          label: panel.repo !== "" ? "Reset “" + panel.repo + "”" : "Remove rename"
          onActivated: panel.act("unalias")
        }
        ActionButton {
          label: "Back to auto"
          enabledState: panel.info !== null && !!panel.info.pinned
          onActivated: panel.act("unpin")
        }
      }

      Text {
        width: parent.width
        text: "Pin sticks to workspace " + panel.wsLabel + ". Rename project follows the repo to any workspace. Enter pins · double-click a suggestion to pin it · Esc cancels."
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: panel.dim
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
  }
}
