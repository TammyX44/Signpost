import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "switcherRank.js" as Rank

KeyboardPanel {
  id: panel

  property var projects: []
  property bool pending: false
  property string selectionError: ""
  // What the bar knows about a project's agent: "" | "work" | "ready".
  property var statusOf: function(project) { return "" }
  property var colors: null          // SignColors: the theme's colours
  // A project's colour (its remembered slot) and a workspace's key as the bar shows it: from the bar.
  property var projectTone: null
  property var keyLabel: function(workspace) { return String(workspace) }
  // "always" · "selected" (git details on the selected row only) · "never"
  property string gitMode: "selected"
  property bool indexEnabled: false
  // How long a project's agent has waited ("4m"), from the bar.
  property var ageOf: function(project) { return "" }
  signal chosen(var project)
  signal action(string kind, var project)   // "terminal" | "editor" | "agent" | "remote"
  signal queryEdited()

  readonly property color ink: Commons.Color.popups.text
  readonly property color surface: {
    var c = Commons.Color.popups.background
    return Qt.rgba(c.r, c.g, c.b, 1)
  }
  // The brightest a row gets (selected: lift plus wash). Secondary text is measured against it, so it
  // reads at 4.5:1 on every row; faint text (headers, separators, hints) at 4.5:1 on the panel.
  readonly property color rowTop: Qt.tint(surface, Util.alpha(ink, 0.17))
  readonly property color muted: colors ? colors.legible(ink, rowTop, 0.72, 4.5) : Util.alpha(ink, 0.75)
  readonly property color faint: colors ? colors.legible(ink, surface, 0.5, 4.5) : Util.alpha(ink, 0.55)
  readonly property color workColor: colors ? colors.workColor : "#7aa2f7"
  readonly property color waitColor: colors ? colors.waitColor : "#e0af68"
  readonly property color readyColor: colors ? colors.readyColor : "#9ece6a"
  readonly property color failedColor: colors ? colors.failedColor : "#f7768e"
  readonly property color endedColor: colors ? colors.endedColor : "#a9b1d6"
  function onRow(color) { return colors ? colors.readable(color, rowTop, 4.5) : color }
  readonly property color markColor: onRow(waitColor)
  readonly property color askColor: onRow(waitColor)
  readonly property color addColor: onRow(colors ? colors.addColor : "#9ece6a")
  readonly property color delColor: onRow(colors ? colors.delColor : "#f7768e")

  function toneFor(project) {
    if (!project || project.hue === undefined || project.hue === null || project.hue < 0) return ink
    return projectTone ? projectTone(project) : (colors ? colors.tone(project.hue) : ink)
  }
  function tintFor(project, alpha) {
    var tinted = !!project && project.hue !== undefined && project.hue !== null && project.hue >= 0
    return Util.alpha(toneFor(project), tinted ? alpha : alpha * 0.5)
  }
  function statusColor(status) {
    return status === "wait" ? waitColor : status === "work" ? workColor : status === "failed" ? failedColor
         : status === "ended" ? endedColor : readyColor
  }
  // A status in words: an agent's, or a command's ("passed" only when the shell reported success).
  function statusWords(status, agent) {
    if (status === "wait") return strings.t("projects.needsYou")
    if (status === "work") return strings.t(agent ? "projects.working" : "projects.running")
    if (status === "failed") return strings.t("projects.failed")
    if (status === "ready" && !agent) return strings.t("projects.passed")
    return strings.t("projects.ready")
  }

  // Names open more than once (the same repo in T3 Code and in a terminal): those rows say where.
  readonly property var repeatedNames: {
    var count = {}, out = {}
    for (var i = 0; i < panel.projects.length; i++) {
      var project = panel.projects[i]
      if (project.workspace <= 0) continue
      var key = project.name.toLocaleLowerCase()
      count[key] = (count[key] || 0) + 1
      if (count[key] > 1) out[key] = true
    }
    return out
  }
  function showsHost(project) {
    return project.workspace > 0 && !!project.host && panel.repeatedNames[project.name.toLocaleLowerCase()] === true
  }

  // A key, drawn as a keycap: a face on a slightly darker lip, so it reads as something to press.
  // (An inline component can't see this file's ids, so it reads the theme itself.)
  component Keycap: Item {
    id: cap
    property string label: ""
    property color ink: Commons.Color.popups.text
    property color surface: Qt.rgba(Commons.Color.popups.background.r, Commons.Color.popups.background.g, Commons.Color.popups.background.b, 1)
    property color face: Util.alpha(cap.ink, 0.07)
    property color lip: Util.alpha(cap.ink, 0.16)
    property color textColor: Util.alpha(cap.ink, 0.7)
    implicitWidth: Math.max(Style.space(18), capText.implicitWidth + Style.space(9))
    implicitHeight: Style.space(18)

    Rectangle { anchors.fill: parent; radius: Style.space(5); color: cap.lip }
    Rectangle {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: 1
      anchors.bottomMargin: 0
      height: parent.height - 2.5
      radius: Style.space(4)
      color: Qt.tint(cap.surface, cap.face)
    }
    Text {
      id: capText
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.verticalCenter: parent.verticalCenter
      anchors.verticalCenterOffset: -0.75
      text: cap.label
      textFormat: Text.PlainText
      color: cap.textColor
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      font.weight: Font.DemiBold
    }
  }
  contentWidth: panel.fittedContentWidth(Style.space(500))
  contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)
  focusTarget: panel.loadedContent ? panel.loadedContent.field : null

  onOpenChanged: if (open && panel.loadedContent) panel.loadedContent.reset()

  property Commons.Translations translations: Commons.Translations { id: strings; directory: Qt.resolvedUrl("../i18n") }

  function groupLabel(group) {
    if (group === "open") return strings.t("projects.open")
    if (group === "projects") return strings.t("projects.projectsGroup")
    return strings.t("projects.recentGroup")
  }

  function filter(query) {
    return Rank.filterProjects(panel.projects, query, panel.statusOf, panel.keyLabel)
  }

  content: Item {
    id: body

    Highlight { id: highlight }

    property alias field: search
    property string selectedId: ""
    property string selectedDirectory: ""     // what you picked, so it is found again under a new id
    readonly property var matches: panel.filter(search.text)
    readonly property var terms: search.text.trim().toLocaleLowerCase().split(/\s+/).filter(function(term) { return term.length > 0 })
    // Nothing picked yet: the first row. A pick stays on its project as the list refreshes (an id names the
    // workspace too, so a project that opens, closes or moves comes back by its folder). When it is gone,
    // nothing is selected, so a key pressed at that moment can't act on whatever moved into its place.
    readonly property int selectedIndex: {
      if (!matches.length) return -1
      if (!body.selectedId) return 0
      var at = matches.findIndex(function(project) { return project.id === body.selectedId })
      if (at < 0 && body.selectedDirectory)
        at = matches.findIndex(function(project) { return project.directory === body.selectedDirectory })
      return at
    }
    readonly property var selected: selectedIndex >= 0 ? matches[selectedIndex] : null
    readonly property bool canRun: selected !== null && selected.directory !== ""
    readonly property bool canRemote: selected !== null && !!selected.remote
    readonly property string remoteHost: {
      var m = canRemote ? /^https:\/\/([A-Za-z0-9.-]+)\//.exec(String(selected.remote)) : null
      return m ? m[1] : ""
    }
    readonly property real rowHeight: Style.space(50)
    readonly property real headerHeight: Style.space(24)
    // A waiting agent's question gets its own line under the name, wrapping to two before it is cut.
    // Its height is worked out here (not measured from the delegate) so the list knows every row's
    // height up front: scrolling, the list's height and keeping the selection in view stay exact.
    readonly property real askWidth: Math.max(Style.space(120), results.width - Style.space(12 + 32 + 12 + 10))
    readonly property real askLine: Math.ceil(askMetrics.height)
    FontMetrics { id: askMetrics; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
    function asks(project) {
      return project.workspace > 0 && !!project.agentMessage && panel.statusOf(project) === "wait"
    }
    function askLines(project) {
      if (!body.asks(project)) return 0
      return askMetrics.advanceWidth(project.agentMessage) > body.askWidth ? 2 : 1
    }
    function heightOf(project) {
      var lines = body.askLines(project)
      return body.rowHeight + (lines ? lines * body.askLine + Style.space(2) : 0)
    }
    readonly property real rowSpacing: Style.space(2)
    readonly property int visibleRows: 7

    // The list is the matches with a header in front of each group. `where[i]` is where
    // match i sits in the list, so the keyboard can move through projects and skip headers.
    readonly property var layout: {
      var entries = [], where = [], height = 0
      function add(entry, entryHeight) {
        entries.push(entry)
        height += entryHeight + (entries.length > 1 ? body.rowSpacing : 0)
      }
      var j = 0
      while (j < matches.length) {
        var group = Rank.groupOf(matches[j])
        var end = j + 1
        while (end < matches.length && Rank.groupOf(matches[end]) === group) end++
        add({ header: true, label: panel.groupLabel(group), count: end - j }, body.headerHeight)
        for (var k = j; k < end; k++) {
          where.push(entries.length)
          add({ header: false, project: matches[k] }, body.heightOf(matches[k]))
        }
        j = end
      }
      return { entries: entries, where: where, height: height }
    }
    readonly property real listHeight: Math.min(layout.height,
      body.visibleRows * body.rowHeight + 2 * body.headerHeight + (body.visibleRows + 1) * body.rowSpacing)

    implicitHeight: column.implicitHeight

    function reset() {
      search.clear()
      body.selectedId = ""
      body.selectedDirectory = ""
      results.positionViewAtBeginning()
    }

    function reveal(next) {
      // The first project in a group shows its header too.
      var at = body.layout.where[next]
      results.positionViewAtIndex(next === 0 || body.layout.entries[at - 1].header ? Math.max(0, at - 1) : at, ListView.Contain)
    }

    function move(step) {
      var count = body.matches.length
      if (count === 0) return
      var next = (body.selectedIndex + step + count) % count
      body.selectedId = body.matches[next].id
      body.selectedDirectory = body.matches[next].directory || ""
      body.reveal(next)
    }

    function activate() {
      if (body.selectedIndex >= 0) panel.chosen(body.matches[body.selectedIndex])
    }

    function run(kind) {
      if (kind === "remote" ? !body.canRemote : !body.canRun) return
      panel.action(kind, body.selected)
    }

    // The last line of a row: its git state, then the folder. Everything is escaped: branch names
    // and folder names are not ours.
    function detailMarkup(project, status, selected) {
      var faint = highlight.hex(panel.faint)
      var parts = []
      if (panel.gitMode === "never" || (panel.gitMode === "selected" && !selected)) return highlight.safe(project.detail)
      if (project.branch) parts.push(highlight.safe("\ue0a0 " + project.branch))
      var changed = []
      if (project.added > 0) changed.push("<font color=\"" + highlight.hex(panel.addColor) + "\">+" + Number(project.added) + "</font>")
      if (project.removed > 0) changed.push("<font color=\"" + highlight.hex(panel.delColor) + "\">−" + Number(project.removed) + "</font>")
      if (changed.length) parts.push(changed.join(" "))
      var sync = (project.ahead > 0 ? "↑" + Number(project.ahead) : "") + (project.behind > 0 ? (project.ahead > 0 ? " " : "") + "↓" + Number(project.behind) : "")
      if (sync) parts.push(sync)
      parts.push(highlight.safe(project.detail))
      return parts.join("<font color=\"" + faint + "\">  ·  </font>")
    }

    ColumnLayout {
      id: column
      anchors.fill: parent
      spacing: Style.space(8)

      TextField {
        id: search
        Layout.fillWidth: true
        foreground: panel.ink
        placeholderText: strings.t("projects.search")
        Accessible.name: placeholderText
        maximumLength: 180
        selectByMouse: true
        rightPadding: horizontalPadding + navHint.width + Style.space(6)
        onTextChanged: {
          body.selectedId = ""
          body.selectedDirectory = ""
          panel.queryEdited()
          Qt.callLater(results.positionViewAtBeginning)
        }
        onAccepted: body.activate()
        Keys.onUpPressed: body.move(-1)
        Keys.onDownPressed: body.move(1)
        Keys.onEscapePressed: panel.close()
        // Ctrl with a letter runs an action. Ctrl+A stays select-all, so the agent is on Ctrl+G.
        Keys.onPressed: function(event) {
          if ((event.modifiers & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier)) !== Qt.ControlModifier) return
          var kind = event.key === Qt.Key_T ? "terminal" : event.key === Qt.Key_E ? "editor"
                   : event.key === Qt.Key_G ? "agent" : event.key === Qt.Key_R ? "remote" : ""
          if (kind === "") return
          event.accepted = true
          body.run(kind)
        }

        // How to move and how to leave, where the eye already is.
        Row {
          id: navHint
          anchors.right: parent.right
          anchors.rightMargin: search.horizontalPadding
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(5)

          Keycap { anchors.verticalCenter: parent.verticalCenter; label: "↑↓" }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: strings.t("projects.hintMove")
            textFormat: Text.PlainText
            color: panel.faint
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          Item { width: Style.space(4); height: 1 }
          Keycap { anchors.verticalCenter: parent.verticalCenter; label: strings.t("keys.esc") }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: strings.t("projects.hintClose")
            textFormat: Text.PlainText
            color: panel.faint
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
      }

      ListView {
        id: results
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredHeight: body.listHeight
        Layout.minimumHeight: visible ? body.rowHeight : 0
        visible: count > 0
        model: body.layout.entries
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        spacing: body.rowSpacing

        delegate: Item {
          id: entry
          required property var modelData
          required property int index
          readonly property bool isHeader: modelData.header === true
          readonly property var project: isHeader ? null : modelData.project
          readonly property bool selected: !isHeader && project.id === (body.matches[body.selectedIndex] || {}).id
          readonly property bool open: !isHeader && project.workspace > 0
          readonly property string status: open ? (panel.statusOf(project) || "") : ""
          readonly property bool isAgentStatus: !isHeader && !!project
            && (status === "wait"
                || (status === "work" && (project.agent === "work" || (project.busy && project.agent !== "done")))
                || (status === "ready" && project.command !== "ready"))
          readonly property int hue: !isHeader && project.hue !== undefined && project.hue !== null ? project.hue : -1
          readonly property int askLines: isHeader ? 0 : body.askLines(project)
          readonly property string workspaceLabel: !isHeader && open
            ? strings.t("projects.workspace").arg(Commons.I18n.formatNumber(project.workspace))
            : (!isHeader && Rank.groupOf(project) === "projects" ? strings.t("projects.indexed") : strings.t("projects.recent"))

          width: ListView.view ? ListView.view.width : 0
          height: isHeader ? body.headerHeight : body.heightOf(project)

          // ---- group header
          RowLayout {
            visible: entry.isHeader
            anchors.fill: parent
            anchors.leftMargin: Style.space(9)
            anchors.rightMargin: Style.space(9)
            anchors.topMargin: Style.space(4)

            Text {
              text: entry.isHeader ? entry.modelData.label.toLocaleUpperCase() : ""
              textFormat: Text.PlainText
              color: panel.faint
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.2
              font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            Text {
              text: entry.isHeader ? Commons.I18n.formatNumber(entry.modelData.count) : ""
              textFormat: Text.PlainText
              color: panel.faint
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }

          // ---- project row
          // Selected: a neutral lift, a wash of the project's colour fading to the right, a hairline and
          // the colour edge. Hover: the neutral lift only, at half strength. The two never look alike.
          Rectangle {
            id: row
            visible: !entry.isHeader
            anchors.fill: parent
            radius: Style.cornerRadius
            readonly property color lift: entry.selected ? Util.alpha(panel.ink, 0.1)
                                        : (click.containsMouse ? Util.alpha(panel.ink, 0.045) : "transparent")
            // What the tile's badge sits on, so its ring matches the row.
            readonly property color under: entry.selected ? Qt.tint(Qt.tint(panel.surface, row.lift), panel.tintFor(entry.project, 0.26))
                                         : Qt.tint(panel.surface, row.lift)
            color: row.lift
            border.width: entry.selected ? 1 : 0
            border.color: panel.tintFor(entry.project, 0.5)

            Rectangle {
              visible: entry.selected
              anchors.fill: parent
              radius: parent.radius
              gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: panel.tintFor(entry.project, 0.3) }
                GradientStop { position: 0.8; color: panel.tintFor(entry.project, 0.04) }
              }
            }

            Accessible.role: Accessible.ListItem
            Accessible.name: entry.isHeader ? "" : entry.project.name
            Accessible.description: entry.isHeader ? "" : (panel.showsHost(entry.project) ? entry.project.host + " · " : "")
              + entry.project.detail + " · " + entry.workspaceLabel
              + (entry.status ? " · " + panel.statusWords(entry.status, entry.isAgentStatus) : "")
              + (entry.status === "wait" && entry.project.agentMessage ? ": " + entry.project.agentMessage : "")
            Accessible.selectable: true
            Accessible.selected: entry.selected
            Accessible.onPressAction: if (!entry.isHeader) panel.chosen(entry.project)

            // The selected project's colour, as an edge.
            Rectangle {
              visible: entry.selected
              x: 0
              anchors.verticalCenter: parent.verticalCenter
              width: 3
              height: parent.height - Style.space(18)
              radius: 1.5
              color: panel.tintFor(entry.project, 1)
            }

            RowLayout {
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.leftMargin: Style.space(12)
              anchors.rightMargin: Style.space(10)
              anchors.topMargin: Style.space(9)
              spacing: Style.space(12)

              ProjectTile {
                Layout.alignment: Qt.AlignTop
                size: Style.space(32)
                tinted: entry.hue >= 0
                tone: panel.toneFor(entry.project)
                ink: panel.colors ? panel.colors.ink(tone) : "#10111a"
                monogram: !entry.isHeader && entry.project.monogram ? entry.project.monogram : ""
                outlined: !entry.open
                status: entry.status
                reduceMotion: Style.reduceMotion
                ring: row.under
                neutral: panel.ink
                workColor: panel.workColor
                waitColor: panel.waitColor
                readyColor: panel.readyColor
                failedColor: panel.failedColor
                endedColor: panel.endedColor
                fontFamily: Style.font.family
              }

              ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: Style.space(2)

                // Line 1: the name (and where it is open, when the name alone is ambiguous), then the
                // agent's state in words and the workspace's key.
                RowLayout {
                  Layout.fillWidth: true
                  spacing: Style.space(8)

                  Text {
                    Layout.fillWidth: true
                    // Escaped: names are window titles and folder names, hosts are app names.
                    text: entry.isHeader ? "" : highlight.markup(entry.project.name, body.terms, panel.markColor, panel.faint)
                      + (panel.showsHost(entry.project) ? "<font color=\"" + highlight.hex(panel.muted) + "\"> · "
                         + highlight.safe(entry.project.host) + "</font>" : "")
                    textFormat: Text.StyledText
                    elide: Text.ElideRight
                    color: panel.ink
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    font.weight: Font.DemiBold
                  }

                  // Agent state, in words, for the colour-blind and for anyone who missed the badge.
                  Rectangle {
                    id: pill
                    visible: entry.status !== ""
                    readonly property color hue: panel.statusColor(entry.status)
                    readonly property color fill: Util.alpha(pill.hue, 0.2)
                    Layout.preferredHeight: Style.space(19)
                    Layout.preferredWidth: pillText.implicitWidth + Style.space(16)
                    radius: height / 2
                    color: pill.fill
                    border.width: 1
                    border.color: Util.alpha(pill.hue, 0.32)

                    Text {
                      id: pillText
                      anchors.centerIn: parent
                      text: (entry.isAgentStatus && entry.project && entry.project.mixedAgents && entry.project.agentLabel ? entry.project.agentLabel + " · " : "")
                          + panel.statusWords(entry.status, entry.isAgentStatus)
                          + (entry.status === "wait" && panel.ageOf(entry.project) ? " · " + panel.ageOf(entry.project) : "")
                      textFormat: Text.PlainText
                      color: panel.colors ? panel.colors.readable(pill.hue, Qt.tint(panel.rowTop, pill.fill), 4.5) : pill.hue
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.DemiBold
                    }
                  }

                  // The workspace, as a key: the number the bar shows, which is also Super + that number.
                  Keycap {
                    visible: entry.open
                    Layout.preferredWidth: Math.max(Style.space(20), implicitWidth)
                    Layout.preferredHeight: Style.space(20)
                    label: entry.open ? panel.keyLabel(entry.project.workspace) : ""
                    textColor: panel.ink
                  }
                }

                // Line 2, only while its agent waits: what it asked, in full where it fits.
                Text {
                  Layout.fillWidth: true
                  Layout.preferredHeight: entry.askLines * body.askLine
                  visible: entry.askLines > 0
                  text: visible ? entry.project.agentMessage : ""
                  textFormat: Text.PlainText   // the agent's words: never rich text
                  wrapMode: Text.Wrap
                  maximumLineCount: 2
                  elide: Text.ElideRight
                  color: panel.askColor
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                }

                // Last line: branch, changes, sync, folder.
                Text {
                  Layout.fillWidth: true
                  text: entry.isHeader ? "" : body.detailMarkup(entry.project, entry.status, entry.selected)
                  textFormat: Text.StyledText
                  elide: Text.ElideRight
                  color: panel.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                }
              }
            }

            MouseArea {
              id: click
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: if (!entry.isHeader) panel.chosen(entry.project)
            }
          }
        }
      }

      Text {
        Layout.fillWidth: true
        Layout.margins: Style.space(6)
        visible: !results.visible
        readonly property bool hasIndex: panel.indexEnabled || panel.projects.some(function(p) { return Rank.groupOf(p) === "projects" })
        text: !panel.projects.length
          ? strings.t("projects.empty")
          : (hasIndex
              ? strings.t("projects.noMatchesIndexed").arg(Commons.I18n.formatNumber(panel.projects.length))
              : strings.t("projects.noMatches"))
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: panel.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }

      // An error or "opening…" when there is one; otherwise what you can do with the selected project.
      Text {
        Layout.fillWidth: true
        visible: panel.selectionError !== "" || panel.pending
        text: {
          if (panel.selectionError === "missingFolder") return strings.t("projects.missingFolder")
          if (panel.selectionError) return strings.t("projects.unavailable")
          return strings.t("projects.opening")
        }
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: panel.selectionError ? Commons.Color.urgent : panel.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }

      Rectangle {
        Layout.fillWidth: true
        Layout.topMargin: -Style.space(2)
        visible: actions.visible
        height: 1
        color: Util.alpha(panel.ink, 0.08)
      }

      // What you can do with the selected project. Enter switches; the rest are Ctrl + a letter,
      // shown once as "Ctrl +" in front of the letters rather than as a cryptic ^T on each.
      Flow {
        id: actions
        Layout.fillWidth: true
        visible: results.visible && panel.selectionError === "" && !panel.pending
        spacing: Style.space(4)

        Repeater {
          model: [
            { kind: "switch", key: "↵", label: body.selected && body.selected.workspace > 0 ? strings.t("projects.switch") : strings.t("projects.actionEditor"), ok: body.selected && (body.selected.workspace > 0 || body.canRun) },
            { kind: "ctrl" },
            { kind: "terminal", key: "T", label: strings.t("projects.actionTerminal"), ok: body.canRun },
            { kind: "editor", key: "E", label: strings.t("projects.actionEditor"), ok: body.canRun },
            { kind: "agent", key: "G", label: strings.t("projects.actionAgent"), ok: body.canRun },
            // The host is part of the label: a repo picks its own remote, and a look-alike such as
            // github.com.example.org should be seen before it is opened.
            { kind: "remote", key: "R", ok: body.canRemote,
              label: body.remoteHost ? strings.t("projects.actionRemote") + " · " + body.remoteHost : strings.t("projects.actionRemote") }
          ]

          Item {
            id: slot
            required property var modelData
            readonly property bool modifier: modelData.kind === "ctrl"
            width: modifier ? ctrlText.implicitWidth + Style.space(8) : chip.width
            height: Style.space(28)

            Text {
              id: ctrlText
              visible: slot.modifier
              anchors.verticalCenter: parent.verticalCenter
              x: Style.space(6)
              text: strings.t("keys.ctrl") + " +"
              textFormat: Text.PlainText
              color: panel.faint
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.weight: Font.DemiBold
            }

            Rectangle {
              id: chip
              visible: !slot.modifier
              readonly property bool primary: slot.modelData.kind === "switch"
              readonly property color accent: body.selected ? panel.toneFor(body.selected) : panel.ink
              readonly property color accentInk: panel.colors ? panel.colors.ink(chip.accent) : "#10111a"
              height: parent.height
              width: chipRow.implicitWidth + Style.space(primary ? 14 : 10)
              radius: Style.space(8)
              opacity: slot.modelData.ok ? 1 : 0.38
              color: primary ? chip.accent : (chipMouse.containsMouse && slot.modelData.ok ? Util.alpha(panel.ink, 0.08) : "transparent")
              Accessible.role: Accessible.Button
              Accessible.name: slot.modifier ? "" : slot.modelData.label

              Row {
                id: chipRow
                anchors.centerIn: parent
                spacing: Style.space(6)

                Keycap {
                  anchors.verticalCenter: parent.verticalCenter
                  label: slot.modifier ? "" : slot.modelData.key
                  face: chip.primary ? Qt.rgba(0, 0, 0, 0.16) : Util.alpha(panel.ink, 0.07)
                  lip: chip.primary ? Qt.rgba(0, 0, 0, 0.3) : Util.alpha(panel.ink, 0.16)
                  surface: chip.primary ? chip.accent : panel.surface
                  textColor: chip.primary ? chip.accentInk : panel.ink
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: slot.modifier ? "" : slot.modelData.label
                  textFormat: Text.PlainText
                  color: chip.primary ? chip.accentInk : panel.ink
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                  font.weight: chip.primary ? Font.Bold : Font.Normal
                }
              }

              MouseArea {
                id: chipMouse
                anchors.fill: parent
                enabled: !slot.modifier && slot.modelData.ok
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: slot.modelData.kind === "switch" ? body.activate() : body.run(slot.modelData.kind)
              }
            }
          }
        }
      }
    }
  }
}
