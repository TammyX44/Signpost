import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// A workspace up close: what it is called, what is open on it, and why it has that name (every
// source, which one won, and why). It may be a project, an app, a host or a model: nothing here
// assumes a git project.
PopupCard {
  id: card

  property var widget: null
  property var info: null
  property int wsLabel: 0
  property string status: ""          // "" | "work" | "wait" | "ready"
  property string commandStatus: ""
  property string age: ""             // how long it has waited ("4m"), while it needs you
  property string downReason: ""      // the engine stopped: why (the card says only that)
  property var hint: null             // {id}: Omarchy's workspace widget is on the bar too
  property var colors: null           // SignColors
  signal replaceChosen()
  signal keepBothChosen()

  triggerMode: "hover"
  contentWidth: card.fittedContentWidth(Style.space(360))
  contentHeight: card.fittedContentHeight(column.implicitHeight)

  readonly property color ink: Commons.Color.popups.text
  // Secondary text at 4.5:1 on the card (on the winner row's lift, its brightest place).
  readonly property color dim: colors ? colors.legible(ink, Qt.tint(surface, Util.alpha(ink, 0.08)), 0.62, 4.5) : Util.alpha(ink, 0.65)
  readonly property color surface: {
    var c = Commons.Color.popups.background
    return Qt.rgba(c.r, c.g, c.b, 1)
  }
  readonly property bool tinted: info !== null && info.hue !== undefined && info.hue !== null
  readonly property color tone: !tinted ? ink : (widget && widget.toneFor ? widget.toneFor(info) : (colors ? colors.tone(info.hue) : ink))
  readonly property string message: info && info.agentMessage ? String(info.agentMessage) : ""
  readonly property string badgeStatus: status === "work" || status === "wait" ? status : (commandStatus || status)
  readonly property string agentLabel: info && info.mixedAgents && info.agentLabel ? info.agentLabel + " · " : ""
  // The pill speaks for whatever the badge shows: an agent that works or waits comes first, then a command.
  readonly property bool commandPill: commandStatus !== "" && status !== "work" && status !== "wait"
  readonly property var commandWords: ({ work: "running", failed: "failed", ready: "passed", ended: "finished" })
  readonly property color statusColor: colors ? colors.statusHue(badgeStatus) : ink
  readonly property color statusText: colors ? colors.readable(statusColor, Qt.tint(surface, Util.alpha(statusColor, 0.16)), 4.5) : statusColor
  readonly property string mono: widget && widget.bar ? widget.bar.fontFamily : Style.font.family
  readonly property var appList: info && Array.isArray(info.appList) ? info.appList : []
  readonly property string title: info ? (info.full || info.name || "") : ""
  // "Open here" earns its place when it says something the title doesn't: more than one app, or the
  // app a project (or a pin) lives in.
  readonly property bool showApps: appList.length > 1 || (appList.length === 1 && appList[0].name !== title)
  readonly property bool down: downReason !== ""
  readonly property var rows: info && Array.isArray(info.rows) ? info.rows : []
  // "Why this name" earns its place when there was a choice: more than one source, a weak name beaten,
  // a pin, or a rename on its way. With one source the sentence at the top already says it all.
  readonly property bool showWhy: !down && info !== null && (rows.length > 1 || !!info.overridden || !!info.pinned || !!info.pending)

  function reanchor() { if (card.anchor) card.anchor.updateAnchor() }
  onAnchorItemChanged: reanchor()
  onInfoChanged: { reanchor(); card.nowMs = Date.now() }

  property real nowMs: Date.now()

  Column {
    id: column
    width: parent.width
    spacing: Style.space(6)

    Timer {
      interval: 1000
      repeat: true
      running: card.open && card.info !== null && !!card.info.pending
      onRunningChanged: if (running) card.nowMs = Date.now()
      onTriggered: card.nowMs = Date.now()
    }

    RowLayout {
      width: parent.width
      spacing: Style.space(10)

      ProjectTile {
        size: Style.space(34)
        tinted: card.tinted && !card.down
        tone: card.tone
        reduceMotion: Style.reduceMotion
        ink: card.colors ? card.colors.ink(card.tone) : "#10111a"
        monogram: card.info && card.info.monogram ? card.info.monogram : ""
        status: card.badgeStatus
        ring: card.surface
        neutral: card.ink
        workColor: card.colors ? card.colors.workColor : "#7aa2f7"
        waitColor: card.colors ? card.colors.waitColor : "#e0af68"
        readyColor: card.colors ? card.colors.readyColor : "#9ece6a"
        failedColor: card.colors ? card.colors.failedColor : "#f7768e"
        endedColor: card.colors ? card.colors.endedColor : "#a9b1d6"
        fontFamily: card.mono

        contentItem: SignIcon {
          widget: card.widget
          anchors.centerIn: parent
          size: parent.width
          spec: card.info ? card.info.icon : null
          glyphKey: card.info && card.info.icon && card.info.icon.kind === "glyph" ? card.info.icon.key : "app"
          tint: card.ink
        }
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          Layout.fillWidth: true
          text: card.down ? "Signpost's engine stopped" : card.title
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: card.ink
          font.family: card.mono
          font.pixelSize: Style.font.body
          font.bold: true
        }
        Text {
          Layout.fillWidth: true
          text: {
            if (card.down) return "Workspace " + card.wsLabel
            var windows = card.info ? Number(card.info.windows) || 0 : 0
            return "Workspace " + card.wsLabel + (windows === 0 ? " · nothing open yet" : windows > 1 ? " · " + windows + " windows" : "")
          }
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: card.dim
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
        }
      }

      Rectangle {
        visible: card.badgeStatus !== "" && !card.down
        Layout.preferredHeight: Style.space(19)
        Layout.preferredWidth: statusText.implicitWidth + Style.space(16)
        radius: height / 2
        color: Util.alpha(card.statusColor, 0.16)

        Text {
          id: statusText
          anchors.centerIn: parent
          text: card.commandPill ? (card.commandWords[card.commandStatus] || "finished")
                : card.agentLabel + (card.status === "wait" ? "needs you" + (card.age ? " · " + card.age : "")
                : (card.status === "work" ? "working" : "finished"))
          textFormat: Text.PlainText
          color: card.statusText
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
    }

    // One plain sentence: what this workspace is, and where.
    Text {
      width: column.width
      visible: !card.down && text !== ""
      text: card.info && card.info.summary ? String(card.info.summary) : ""
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.ink
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      width: column.width
      visible: !card.down && text !== ""
      text: card.info && card.commandStatus ? (card.info.commandText || "") : ""
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.colors ? card.colors.readable(card.colors.statusHue(card.commandStatus), card.surface, 4.5) : card.ink
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      width: column.width
      visible: !card.down && text !== ""
      text: card.info && card.info.unread > 0
            ? (card.info.unread > 99 ? "99+" : card.info.unread) + " unread (from the window title)" : ""
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.dim
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    // The engine stopped: say so plainly, and why, in its own words.
    Text {
      width: column.width
      visible: card.down
      text: "The bar shows plain workspace numbers until it is back. It restarts by itself; "
            + "omarchy-shell signpost status says when it is up."
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.ink
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      width: column.width
      visible: card.down
      text: card.downReason
      textFormat: Text.PlainText
      wrapMode: Text.WrapAnywhere
      maximumLineCount: 4
      elide: Text.ElideRight
      color: card.dim
      font.family: card.mono
      font.pixelSize: Style.font.caption
    }

    // What the agent is asking, when it is waiting for you.
    Rectangle {
      width: column.width
      visible: card.status === "wait" && card.message !== ""
      implicitHeight: askText.implicitHeight + Style.space(14)
      radius: Style.cornerRadius
      color: Util.alpha(card.statusColor, 0.1)

      Rectangle { width: 2; height: parent.height - Style.space(10); anchors.verticalCenter: parent.verticalCenter; x: Style.space(1); radius: 1; color: card.statusColor }
      Text {
        id: askText
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Style.space(10)
        anchors.rightMargin: Style.space(8)
        text: card.message
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: card.ink
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
    }

    // Omarchy's own workspace numbers are on this bar too: offer to replace them (the same change as
    // the installer's --swap, undone with `signpost.py unswap`). Nothing changes without a click.
    Rectangle {
      width: column.width
      visible: !card.down && card.hint !== null
      implicitHeight: hintColumn.implicitHeight + Style.space(16)
      radius: Style.cornerRadius
      color: Util.alpha(card.ink, 0.06)
      border.width: 1
      border.color: Util.alpha(card.ink, 0.12)

      Column {
        id: hintColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.margins: Style.space(10)
        spacing: Style.space(8)

        Text {
          width: parent.width
          text: "Omarchy's workspace numbers are on this bar too. Replace them with Signpost? You can undo it."
          textFormat: Text.PlainText
          wrapMode: Text.WordWrap
          color: card.ink
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
        }
        Row {
          spacing: Style.space(6)
          Repeater {
            model: [{ key: "replace", label: "Replace", primary: true }, { key: "keep", label: "Keep both", primary: false }]
            Rectangle {
              id: hintButton
              required property var modelData
              height: Style.space(24)
              width: hintLabel.implicitWidth + Style.space(18)
              radius: Style.space(7)
              color: modelData.primary ? card.ink : (hintMouse.containsMouse ? Util.alpha(card.ink, 0.1) : Util.alpha(card.ink, 0.05))
              Accessible.role: Accessible.Button
              Accessible.name: modelData.label
              Text {
                id: hintLabel
                anchors.centerIn: parent
                text: hintButton.modelData.label
                textFormat: Text.PlainText
                color: hintButton.modelData.primary ? card.surface : card.ink
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
                font.weight: hintButton.modelData.primary ? Font.DemiBold : Font.Normal
              }
              MouseArea {
                id: hintMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: hintButton.modelData.key === "replace" ? card.replaceChosen() : card.keepBothChosen()
              }
            }
          }
        }
      }
    }

    // What is open here, at a glance: each app once, most recently used first.
    Text {
      visible: card.showApps && !card.down
      text: "OPEN HERE"
      topPadding: Style.space(4)   // a section starts a little apart from the one before it
      textFormat: Text.PlainText
      color: card.dim
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      font.letterSpacing: 1.2
      font.weight: Font.DemiBold
    }
    Flow {
      width: column.width
      visible: card.showApps && !card.down
      spacing: Style.space(5)

      Repeater {
        model: card.showApps ? card.appList : []

        Rectangle {
          id: appChip
          required property var modelData
          height: Style.space(22)
          width: appRow.implicitWidth + Style.space(12)
          radius: Style.space(6)
          color: Util.alpha(card.ink, 0.07)

          Row {
            id: appRow
            anchors.centerIn: parent
            spacing: Style.space(6)

            SignIcon {
              anchors.verticalCenter: parent.verticalCenter
              widget: card.widget
              size: Style.space(14)
              spec: ({ kind: "app", name: appChip.modelData.icon })
              glyphKey: "app"
              tint: card.ink
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: appChip.modelData.name
              textFormat: Text.PlainText   // app names come from desktop files: never rich text
              color: card.ink
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              visible: appChip.modelData.count > 1
              text: "×" + appChip.modelData.count
              textFormat: Text.PlainText
              color: card.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }
      }
    }

    Text {
      visible: card.showWhy
      text: "WHY THIS NAME"
      topPadding: Style.space(4)   // a section starts a little apart from the one before it
      textFormat: Text.PlainText
      color: card.dim
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      font.letterSpacing: 1.2
      font.weight: Font.DemiBold
    }
    // The answer first, in a sentence; the sources it was chosen from below.
    Text {
      width: column.width
      visible: card.showWhy && text !== ""
      text: card.info ? (card.info.reason || "") : ""
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.ink
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    Repeater {
      model: card.showWhy ? card.rows : []

      Rectangle {
        id: rowItem
        required property var modelData
        width: column.width
        implicitHeight: rowLayout.implicitHeight + Style.space(8)
        radius: Style.cornerRadius
        color: modelData.winner ? Util.alpha(card.ink, 0.08) : "transparent"

        RowLayout {
          id: rowLayout
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          anchors.leftMargin: Style.space(6)
          anchors.rightMargin: Style.space(6)
          spacing: Style.space(8)

          SignIcon {
            widget: card.widget
            spec: rowItem.modelData.icon
            glyphKey: rowItem.modelData.icon && rowItem.modelData.icon.kind === "glyph" ? rowItem.modelData.icon.key : "app"
            tint: card.ink
          }
          Text {
            Layout.fillWidth: true
            text: rowItem.modelData.name
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: card.ink
            font.family: card.mono
            font.pixelSize: Style.font.body
          }
          Text {
            Layout.maximumWidth: column.width * 0.45
            text: rowItem.modelData.detail
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: card.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          // The one that gave the name says so; the others need no label.
          Text {
            visible: rowItem.modelData.winner
            text: "chosen"
            textFormat: Text.PlainText
            color: card.ink
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.weight: Font.DemiBold
          }
        }
      }
    }

    Rectangle { visible: !card.down; width: column.width; height: 1; color: Util.alpha(card.ink, 0.15) }
    Text {
      width: column.width
      visible: card.info !== null && card.info.more > 0
      text: card.info ? "+" + card.info.more + ": other project" + (card.info.more > 1 ? "s" : "") + " on this workspace (plain apps don't count)." : ""
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.dim
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      width: column.width
      visible: card.info !== null && !!card.info.pending
      text: card.info && card.info.pending ? "You focused another window: renames to “" + card.info.pending.name + "” in " + Math.max(0, Math.round((card.info.pending.due - card.nowMs) / 1000)) + " s unless you go back." : ""
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.ink
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
      font.italic: true
    }
    Text {
      width: column.width
      visible: !card.down
      text: "Left-click switches · right-click to pin or rename"
      textFormat: Text.PlainText
      color: card.dim
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
    }
  }
}
