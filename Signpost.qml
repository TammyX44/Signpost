import QtQuick
import QtQuick.Layouts
import QtQuick.Effects
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "components"

// Signpost: the stock Workspaces widget, plus a name for each workspace.
// The names come from signpost.py (long-running, JSON lines on stdout). This
// file only draws them, fits them to the room it has, and edits pins. If the
// engine is down, it is the stock widget: plain numbers.
BarWidget {
  id: root
  moduleName: "io.github.tammyx44.signpost"

  // ------------------------------------------------------------------ stock workspace logic
  // Copied from omarchy.workspaces so numbering, monitors, scrolling and the
  // layout glyph behave exactly as before.
  readonly property int slots: 10

  readonly property var monitor: {
    var window = root.QsWindow.window
    var screen = window ? window.screen : null
    return screen ? Hyprland.monitorFor(screen) : Hyprland.focusedMonitor
  }
  readonly property string monitorName: root.monitor ? root.monitor.name : ""
  property var blocks: ({})

  function readBlocks(text) {
    var next = {}
    var lines = String(text || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var fields = lines[i].split("\t")
      if (fields.length >= 3 && fields[1] !== "") next[fields[1]] = parseInt(fields[0], 10)
    }
    root.blocks = next
  }

  FileView {
    path: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/omarchy/monitor-workspaces"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.readBlocks(text())
    onLoadFailed: root.readBlocks("")
  }

  function blockOf(id) { return Math.floor((id - 1) / root.slots) }
  function slotOf(id) { return (id - 1) % root.slots + 1 }
  function activeWorkspaceId() {
    return root.monitor && root.monitor.activeWorkspace ? root.monitor.activeWorkspace.id : -1
  }

  readonly property int block: {
    var block = root.blocks[root.monitorName]
    if (block !== undefined) return block
    var active = root.activeWorkspaceId()
    return active > 0 ? root.blockOf(active) : 0
  }

  function workspaceById(id) {
    var values = Hyprland.workspaces.values
    for (var i = 0; i < values.length; i++) if (values[i].id === id) return values[i]
    return null
  }

  // Stock: the first five, any others that exist, then visiting workspaces.
  // Signpost adds pinned workspaces of this monitor's block, shown dimmed.
  function workspaceIds() {
    var base = root.block * root.slots
    var ids = [base + 1, base + 2, base + 3, base + 4, base + 5]
    var guests = []
    var values = Hyprland.workspaces.values
    for (var i = 0; i < values.length; i++) {
      var id = values[i].id
      if (id <= 0) continue
      if (root.blockOf(id) === root.block) {
        if (ids.indexOf(id) === -1) ids.push(id)
      } else if (values[i].monitor && values[i].monitor.name === root.monitorName) {
        guests.push(id)
      }
    }
    for (var k = 0; k < root.plannedIds.length; k++) {
      var pid = root.plannedIds[k]
      if (root.blockOf(pid) === root.block && ids.indexOf(pid) === -1) ids.push(pid)
    }
    ids.sort(function(a, b) { return a - b })
    guests.sort(function(a, b) { return a - b })
    return ids.concat(guests)
  }

  // Straight over Hyprland's socket: no shell, no hyprctl process per click.
  function dispatch(expression) {
    Hyprland.dispatch(expression)
  }

  // The row's workspaces. Reassigned only when the list really changes, so the
  // Repeater keeps its cells (and their icons) instead of rebuilding them.
  property var plannedIds: []
  property var wsIds: []
  property string wsIdsKey: ""
  readonly property var computedIds: root.workspaceIds()
  onComputedIdsChanged: {
    var key = JSON.stringify(root.computedIds)
    if (key === root.wsIdsKey) return
    root.wsIdsKey = key
    root.wsIds = root.computedIds
  }

  function focusWorkspace(id) {
    if (root.blockOf(id) === root.block) {
      root.dispatch("o.workspaces.focus(" + root.slotOf(id) + ", \"" + root.monitorName + "\")")
    } else {
      root.dispatch("hl.dsp.focus({ workspace = \"" + id + "\" })")
    }
  }

  property real wheelAccumulator: 0
  function scrollWorkspaces(delta) {
    var wheel = Util.wheelSteps(root.wheelAccumulator, delta)
    root.wheelAccumulator = wheel.remainder
    if (wheel.steps === 0) return
    var ids = root.wsIds
    var index = ids.indexOf(root.activeWorkspaceId())
    if (index === -1) return
    var target = ids[Util.clamp(index - wheel.steps, 0, ids.length - 1)]
    if (target !== ids[index]) root.focusWorkspace(target)
  }

  property string activeMode: "dwindle"
  property bool modeStale: false
  function modeGlyph(mode) {
    if (mode === "scrolling") return "󰝥"
    if (mode === "floating") return "󰔶"
    return "󱓻"
  }
  property var modeCache: ({})
  property int modeGeneration: 0
  property int modeProcWorkspace: -1
  property int modeProcGeneration: 0

  function showActiveMode() {
    var mode = root.modeCache[root.activeWorkspaceId()]
    if (mode !== undefined) root.activeMode = mode
    else readMode()
  }
  function readMode() {
    if (modeProc.running) { root.modeStale = true; return }
    var id = root.activeWorkspaceId()
    if (id === -1) return
    root.modeProcWorkspace = id
    root.modeProcGeneration = root.modeGeneration
    modeProc.command = ["omarchy-hyprland-workspace-layout-current", String(id)]
    modeProc.running = true
  }
  function refreshMode() {
    root.modeCache = ({})
    root.modeGeneration++
    readMode()
  }
  function cycleLayout() {
    if (!root.bar) return
    var command = "omarchy-hyprland-workspace-layout-toggle"
    if (root.monitor && !root.monitor.focused) {
      command = "hyprctl dispatch " + Util.shellQuote("hl.dsp.focus({ monitor = \"" + root.monitorName + "\" })") + " >/dev/null && " + command
    }
    root.bar.run(command)
  }

  Component.onCompleted: refreshMode()
  onMonitorChanged: root.showActiveMode()

  // Which workspaces were used most recently, for shrinking the others first.
  property var seen: ({})
  property int seenClock: 0
  function markSeen() {
    var id = root.activeWorkspaceId()
    if (id <= 0) return
    var next = Object.assign({}, root.seen)
    next[id] = ++root.seenClock
    root.seen = next
  }

  Connections {
    target: root.monitor
    function onActiveWorkspaceChanged() { root.showActiveMode(); root.markSeen(); root.updateAgents() }
  }

  Connections {
    target: Hyprland
    function onRawEvent(event) {
      var name = event ? String(event.name) : ""
      if (name === "configreloaded") root.refreshMode()
      var switched = name === "workspacev2" && root.monitor !== null && root.monitor.focused
      if (switched || name === "moveworkspacev2" || name === "changeworkspaceid") {
        Hyprland.refreshMonitors()
        Hyprland.refreshWorkspaces()
      }
    }
  }

  Process {
    id: modeProc
    stdout: StdioCollector {
      onStreamFinished: {
        var mode = text.trim()
        if (!mode || root.modeProcGeneration !== root.modeGeneration) return
        root.modeCache[root.modeProcWorkspace] = mode
        if (root.modeProcWorkspace === root.activeWorkspaceId()) root.activeMode = mode
      }
    }
    onExited: {
      if (!root.modeStale) return
      root.modeStale = false
      Qt.callLater(root.showActiveMode)
    }
  }

  // ------------------------------------------------------------------ settings
  // This widget's entry in shell.json goes to the engine as it is. The engine checks every value,
  // reads the keys of Signpost 0.3 when the new ones are absent (it never rewrites the file), and sends
  // the settings back with each update, so the bar and the engine always agree. Until the first reply
  // the defaults apply (the engine's names are not shown before it replies anyway).
  readonly property var defaultCfg: ({
    names: "smart", fit: true, style: "tiles", density: "comfortable", showNumbers: true, showMore: true,
    plainWorkspaces: [], ignoreClasses: "", agentBadge: true, badgeStates: ["working", "needsYou", "ready"],
    alerts: true, showQuestions: true, hideWhenSharing: true, commandStatus: true, unreadApps: [], longCommandSeconds: 30, hoverCard: true, switcherGit: "selected",
    switcherRecents: true, projectRoots: ["~/Projects"], recentIgnore: [], agentCommand: "claude", maxWidthPercent: 40
  })
  property var cfg: defaultCfg
  readonly property string nameMode: String(root.cfg.names)
  readonly property bool fitNames: root.cfg.fit !== false
  readonly property string look: String(root.cfg.style)            // tiles · logos · mono · minimal
  readonly property bool compact: root.cfg.density === "compact"
  readonly property bool showNumbers: root.cfg.showNumbers !== false
  readonly property bool showMore: root.cfg.showMore !== false
  readonly property var plainIds: Array.isArray(root.cfg.plainWorkspaces) ? root.cfg.plainWorkspaces : []
  readonly property var badgeStates: Array.isArray(root.cfg.badgeStates) ? root.cfg.badgeStates : []
  readonly property bool hoverOn: root.cfg.hoverCard !== false
  readonly property string switcherGit: String(root.cfg.switcherGit)
  readonly property bool indexEnabled: Array.isArray(root.cfg.projectRoots) && root.cfg.projectRoots.length > 0
  readonly property int maxWidthPercent: Number(root.cfg.maxWidthPercent) || 40
  readonly property bool hideWhenSharing: root.cfg.hideWhenSharing !== false
  readonly property bool commandStatus: root.cfg.commandStatus !== false
  readonly property bool busyDot: root.cfg.agentBadge !== false
  readonly property string agentCommand: String(root.cfg.agentCommand || "claude")
  // Coloured letter tiles, or the older row of logos (style logos / mono / minimal).
  readonly property bool tiles: root.look === "tiles"
  // For SignIcon: "mono" tints logos to the theme, "none" draws none.
  readonly property string iconStyle: root.look === "mono" ? "mono" : (root.look === "minimal" ? "none" : "color")
  onSettingsChanged: root.send({ cmd: "settings", settings: root.settings || {} })

  // A badge state, or "" when the badge is off or that state isn't one you chose to see.
  function shownStatus(state, command) {
    if (!(command ? root.commandStatus : root.busyDot) || !state) return ""
    var key = state === "work" ? "working" : (state === "wait" ? "needsYou" : "ready")
    return root.badgeStates.indexOf(key) !== -1 ? state : ""
  }

  // ------------------------------------------------------------------ theme colours
  // Tiles and badges take their colours from the Omarchy theme (colors.toml), so they follow it.
  property string themeText: ""
  SignColors { id: signColors; text: root.themeText }
  readonly property var tileColors: signColors     // for the hover card and the switcher, and the harness
  // The engine draws an alert's picture in the project's own tile colour: it needs the palette.
  readonly property string tilePairsKey: JSON.stringify(signColors.tilePairs)
  onTilePairsKeyChanged: root.send({ cmd: "palette", tiles: signColors.tilePairs })
  // A project's colour: its remembered slot, else its hue.
  function toneFor(info) {
    if (!info || info.hue === undefined || info.hue === null || info.hue < 0) return root.fg
    return signColors.tone(info.slot !== undefined && info.slot !== null ? info.slot : info.hue)
  }
  FileView {
    id: themeFile
    path: Commons.Color.currentThemePath + "/colors.toml"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.themeText = text()
    onLoadFailed: root.themeText = ""
  }
  // The theme folder is swapped as a whole, so the watched file alone can miss a theme change.
  Connections {
    target: Commons.Color
    function onAccentChanged() { themeFile.reload() }
    function onBackgroundChanged() { themeFile.reload() }
  }

  // ------------------------------------------------------------------ engine
  readonly property string engineScript: decodeURIComponent(String(Qt.resolvedUrl("signpost.py")).replace(/^file:\/\//, ""))
  property var names: ({})
  property var projects: []
  property string projectsKey: ""
  property var iconOverrides: ({})
  property bool engineUp: false
  property bool sharing: false
  property real probeMs: 0
  property int restarts: 0
  property string lastLog: ""
  property string lastLine: ""
  property string claudeLine: ""    // "claude: hook installed + agents view", "claude: none", …
  property string claudeDetail: ""  // the same, in a sentence, with what to do about it
  property string othersLine: ""    // "codex: hook installed · opencode: CPU only", or ""
  property var barHint: null        // {id}: Omarchy's workspace widget is on the bar too
  property real downSince: 0        // when the engine stopped (ms), 0 while it runs
  property bool downShown: false    // stopped for a few seconds: the bar says so, quietly
  readonly property bool namesHidden: !root.engineUp || (root.sharing && root.hideWhenSharing)

  function send(obj) {
    if (engine.running) engine.write(JSON.stringify(obj) + "\n")
  }

  function handleLine(line) {
    if (line === root.lastLine) return
    var data
    try { data = JSON.parse(line) } catch (e) { return }
    if (data && data.recentSelection) {
      root.handleRecentSelection(data.recentSelection)
      return
    }
    if (data && data.jump !== undefined) {
      // "Go to it" on an agent's alert
      if (Number.isSafeInteger(data.jump) && data.jump > 0) root.focusWorkspace(data.jump)
      return
    }
    if (!data || !data.workspaces) return
    root.lastLine = line
    var next = {}
    var planned = []
    for (var i = 0; i < data.workspaces.length; i++) {
      var w = data.workspaces[i]
      if (!w || !w.name) continue
      next[String(w.id)] = w
      if (w.planned) planned.push(w.id)
    }
    if (JSON.stringify(planned) !== JSON.stringify(root.plannedIds)) root.plannedIds = planned
    root.names = next
    var projects = Array.isArray(data.projects) ? data.projects : []
    var projectsKey = JSON.stringify(projects)
    if (projectsKey !== root.projectsKey) {
      root.projectsKey = projectsKey
      root.projects = projects
    }
    var overrides = data.iconOverrides || {}
    if (JSON.stringify(overrides) !== JSON.stringify(root.iconOverrides)) root.iconOverrides = overrides
    root.sharing = !!data.sharing
    root.claudeLine = typeof data.claude === "string" ? data.claude : ""
    root.claudeDetail = typeof data.claudeDetail === "string" ? data.claudeDetail : ""
    root.othersLine = typeof data.others === "string" ? data.others : ""
    root.barHint = data.hint && data.hint.id ? data.hint : null
    if (data.settings && JSON.stringify(data.settings) !== JSON.stringify(root.cfg)) root.cfg = data.settings
    root.probeMs = Number(data.probeMs) || 0
    root.engineUp = true
    root.downSince = 0
    root.downShown = false
    downTimer.stop()
    root.updateAgents()
  }

  Process {
    id: engine
    command: ["python3", "-I", root.engineScript, "watch"]
    running: true
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { root.handleLine(line) } }
    stderr: SplitParser { onRead: function(line) { root.lastLog = line } }
    onStarted: {
      root.send({ cmd: "settings", settings: root.settings || {} })
      root.send({ cmd: "details", on: root.projectsOpen && !root.namesHidden })
      root.send({ cmd: "palette", tiles: signColors.tilePairs })
      stableTimer.restart()
    }
    onExited: function(code) {
      // Never break the bar: plain numbers until it is back.
      root.engineUp = false
      if (!root.downSince) root.downSince = Date.now()
      downTimer.restart()
      root.names = ({})
      root.projects = []
      root.projectsKey = ""
      root.lastLine = ""
      root.updateAgents()
      stableTimer.stop()
      var waits = [1000, 5000, 30000]
      restartTimer.interval = waits[Math.min(root.restarts, waits.length - 1)]
      root.restarts++
      if (root.restarts === 1) console.warn("signpost: engine exited (" + code + "): " + root.lastLog)
      restartTimer.start()
    }
  }

  Timer { id: restartTimer; onTriggered: engine.running = true }
  // A restart takes about a second; only a longer outage earns the marker.
  Timer { id: downTimer; interval: 4000; onTriggered: root.downShown = !root.engineUp }
  readonly property string downReason: {
    var log = String(root.lastLog || "").replace(/^signpost: /, "")
    return log ? log : "It exited without saying why."
  }
  Timer { id: stableTimer; interval: 30000; onTriggered: root.restarts = 0 }

  // ------------------------------------------------------------------ activity
  // The engine reports agent lifecycle events and terminal foreground commands separately.
  // This settle time is only for agents tracked by CPU use.
  readonly property int settleMs: 6000
  property var agentStatus: ({})      // workspace id (string) → "work" | "wait" | "ready"
  property var lastBusy: ({})         // workspace id → ms when it was last seen working
  property var readyIds: ({})         // workspace id → true until you look at it

  function updateAgents() {
    var now = Date.now()
    var active = String(root.activeWorkspaceId())
    var status = {}
    var settling = false
    for (var id in root.names) {
      var info = root.names[id]
      var hooked = info && info.agent ? info.agent : ""
      if (hooked === "wait") {
        root.lastBusy[id] = now
        delete root.readyIds[id]
        status[id] = "wait"
        continue
      }
      if (hooked === "work" || (hooked !== "done" && info && info.busy)) {
        root.lastBusy[id] = now
        delete root.readyIds[id]
        status[id] = "work"
        continue
      }
      if (root.lastBusy[id] !== undefined) {
        if (hooked !== "done" && now - root.lastBusy[id] < root.settleMs) {
          status[id] = "work"
          settling = true
          continue
        }
        delete root.lastBusy[id]
        if (id !== active) root.readyIds[id] = true
      }
      if (root.readyIds[id]) {
        if (id === active) delete root.readyIds[id]
        else status[id] = "ready"
      }
    }
    for (var gone in root.lastBusy) if (!root.names[gone]) delete root.lastBusy[gone]
    for (var gone2 in root.readyIds) if (!root.names[gone2]) delete root.readyIds[gone2]
    if (JSON.stringify(status) !== JSON.stringify(root.agentStatus)) root.agentStatus = status
    settleTimer.running = settling
  }

  Timer { id: settleTimer; interval: 1000; repeat: true; onTriggered: root.updateAgents() }

  // One project's badge in the switcher. A project whose own agent reported in is exact. Otherwise
  // it shares its workspace's state when it is the project that names the workspace.
  function projectStatus(project) {
    var agent = root.shownStatus(root.projectState(project))
    if (agent === "wait" || agent === "work") return agent
    var cmd = root.projectCommandStatus(project)
    return cmd || agent
  }
  function projectCommandStatus(project) {
    if (!project || project.workspace <= 0 || !project.command) return ""
    if (project.command !== "work" && project.workspace === root.activeWorkspaceId()) return ""
    return root.shownStatus(project.command, true)
  }
  function projectState(project) {
    if (!project || project.workspace <= 0) return ""
    if (project.agent === "wait") return "wait"
    if (project.agent === "work" || (project.busy && project.agent !== "done")) return "work"
    var info = root.names[String(project.workspace)]
    var state = root.agentStatus[String(project.workspace)] || ""
    if (project.agent === "done") return state === "ready" ? "ready" : ""
    return state && info && info.aliasKey && info.aliasKey === project.aliasKey ? state : ""
  }

  function workspaceAgentStatus(id) {
    return root.shownStatus(root.agentStatus[String(id)] || "")
  }

  function workspaceCommandStatus(id) {
    var info = root.names[String(id)]
    var state = info ? info.command : ""
    if (state !== "work" && id === root.activeWorkspaceId()) return ""
    return root.shownStatus(state, true)
  }

  function workspaceStatus(id) {
    var agent = root.workspaceAgentStatus(id)
    return agent === "wait" || agent === "work" ? agent : (root.workspaceCommandStatus(id) || agent)
  }

  function cellLabel(info, level) {
    if (!info) return ""
    var name = level === 1 ? info.short : info.name
    return name + (level === 0 && info.mixedAgents && info.agent === "wait" && info.agentLabel
                   && root.shownStatus("wait") ? " · " + info.agentLabel : "")
  }

  function unreadLabel(info) {
    return info && info.unread > 0 ? (info.unread > 99 ? "99+" : String(info.unread)) : ""
  }

  function unreadWidth(info) {
    return root.textWidth(root.unreadLabel(info), true) + Style.space(root.compact ? 6 : 8)
  }

  // How long something has waited, short: "now", "4m", "2h". Seconds since the epoch in, from the engine.
  property real nowSec: Date.now() / 1000
  Timer { interval: 20000; repeat: true; running: root.engineUp; onTriggered: root.nowSec = Date.now() / 1000 }
  function ageText(ts) {
    var secs = Math.max(0, root.nowSec - Number(ts || 0))
    if (!ts || secs < 60) return "now"
    if (secs < 3600) return Math.floor(secs / 60) + "m"
    if (secs < 86400) return Math.floor(secs / 3600) + "h"
    return Math.floor(secs / 86400) + "d"
  }

  // ------------------------------------------------------------------ fit to width
  // Names fit the room the widget may take (maxWidthPercent of the screen). When they don't, the least
  // recently used workspaces shrink first: short name, then logo only, then the number. The focused one
  // goes last. What a cell shows at each level is decided once, in parts(), for the row and for
  // cellWidth() alike, so the prediction is the layout.
  readonly property real gap: Style.spaceReal(root.compact ? 3 : 4)
  readonly property real cellPad: Style.spaceReal(root.compact ? 4 : 6)
  readonly property real minCell: Style.space(root.compact ? 16 : 20)
  readonly property real iconSize: Math.round(Style.font.body * (root.compact ? 0.95 : 1.05))
  // What sits between the number and the name: a tile, or the old logo.
  readonly property real markSize: root.tiles ? Math.round(Style.font.body * (root.compact ? 1.12 : 1.4)) : root.iconSize
  // The tile's width in the row: its size plus the room its status badge leans into, so the badge
  // never touches the name. Measured from a tile that is never shown, so the row and cellWidth()
  // can't disagree about it.
  readonly property real markWidth: root.tiles ? markProbe.implicitWidth : root.iconSize
  ProjectTile { id: markProbe; visible: false; size: root.markSize; reserveBadge: root.busyDot || root.commandStatus }
  readonly property color fg: bar ? bar.barForeground : Commons.Color.foreground
  // The bar's own surface, opaque, so a badge can cut a clean ring out of it.
  readonly property color surface: {
    var c = Commons.Color.bar.background
    return Qt.rgba(c.r, c.g, c.b, 1)
  }

  FontMetrics { id: fm; font.family: root.bar ? root.bar.fontFamily : Style.font.family; font.pixelSize: Style.font.body }
  FontMetrics { id: fmSmall; font.family: root.bar ? root.bar.fontFamily : Style.font.family; font.pixelSize: Style.font.bodySmall }

  readonly property real budget: {
    var window = root.QsWindow.window
    var screen = window ? window.screen : null
    var width = screen ? screen.width : 1920
    return width * Math.max(10, Math.min(90, root.maxWidthPercent)) / 100
  }

  function numberText(id) {
    var shown = root.monitor !== null && root.monitor.activeWorkspace !== null && root.monitor.activeWorkspace.id === id
    var slot = root.slotOf(id)
    return shown ? root.modeGlyph(root.activeMode) : (slot === root.slots ? "0" : String(slot))
  }

  // Labels are sized with these same numbers, so the prediction is the layout.
  function textWidth(text, small) { return Math.ceil((small ? fmSmall : fm).advanceWidth(String(text || ""))) }

  // The workspace under the pointer, at once (the hover card waits; names on hover don't).
  property int pointerId: -1

  // The least a workspace shows, whatever the room: 3 the number only (plain workspaces, a vertical
  // bar, names hidden), otherwise 0. textOff: its logo but no name, by the names setting. Smart names
  // leave a plain app (kitty, a browser, an editor with no project) to its logo, so the names on the
  // bar are the projects', hosts', models' and web apps'.
  function floorOf(id) {
    var info = root.names[String(id)]
    return (!info || root.namesHidden || root.vertical || root.plainIds.indexOf(id) !== -1) ? 3 : 0
  }
  function textOff(id) {
    var info = root.names[String(id)]
    if (!info) return true
    if (root.nameMode === "none") return true
    if (root.nameMode === "smart") return info.source === "app"
    if (root.nameMode === "focused") return id !== root.activeWorkspaceId()
    if (root.nameMode === "hover") return id !== root.pointerId
    return false
  }

  // What a cell shows at a level (0 full · 1 short · 2 logo · 3 number).
  function parts(id, level) {
    var info = root.names[String(id)] || null
    var mark = info !== null && level <= 2 && root.look !== "minimal"
    var text = info !== null && level <= 1 && !root.textOff(id)
    var more = info !== null && level === 0 && root.showMore && info.more > 0 && (text || mark)
    var unread = info !== null && level <= 2 && root.showMore && !!root.unreadLabel(info) && (text || mark)
    var dot = info !== null && !root.tiles && (mark || text) && !!root.workspaceStatus(id)
    // Compact: a tile stands in for its number. showNumbers off: only where something else shows.
    var number = !((mark && root.compact && root.tiles) || (!root.showNumbers && (mark || text)))
    return { number: number, mark: mark, text: text, more: more, unread: unread, dot: dot }
  }

  // Matches the cell's Layout.preferredWidth, plus the grid's column spacing.
  function cellWidth(id, level) {
    var info = root.names[String(id)]
    var p = root.parts(id, level)
    var w = 0, n = 0
    function add(x) { w += (n++ ? root.gap : 0) + x }
    if (p.number) add(root.textWidth(root.numberText(id)))
    if (p.mark) add(root.markWidth)
    if (p.text) add(root.textWidth(root.cellLabel(info, level)))
    if (p.more) add(root.textWidth("+" + info.more, true))
    if (p.unread) add(root.unreadWidth(info))
    if (p.dot) add(Style.space(5))
    return Math.max(root.minCell, w + root.cellPad * 2) + Style.space(1)
  }

  function keepPriority(id) {
    var st = root.workspaceStatus(id)
    if (st === "wait") return 3
    if (st === "failed" || st === "work") return 2
    if (st === "ready" || st === "ended" || (root.showMore && !!root.unreadLabel(root.names[String(id)]))) return 1
    return 0
  }

  // 0 full · 1 short · 2 logo · 3 number
  readonly property var levels: {
    var ids = root.wsIds
    var out = {}
    var active = root.activeWorkspaceId()
    var named = []
    for (var i = 0; i < ids.length; i++) {
      out[ids[i]] = root.floorOf(ids[i])
      if (out[ids[i]] < 3) named.push(ids[i])
    }
    if (!root.fitNames || root.vertical) return out
    function total() {
      var sum = 0
      for (var j = 0; j < ids.length; j++) sum += root.cellWidth(ids[j], out[ids[j]])
      return sum
    }
    var seen = root.seen
    var others = named.filter(function(id) { return id !== active })
    others.sort(function(a, b) {
      var pa = root.keepPriority(a), pb = root.keepPriority(b)
      return pa !== pb ? pa - pb : (seen[a] || 0) - (seen[b] || 0)
    })
    for (var lv = 1; lv <= 3; lv++) {
      for (var k = 0; k < others.length; k++) {
        if (total() <= root.budget) return out
        if (lv === 3 && root.keepPriority(others[k]) >= 2) continue
        out[others[k]] = Math.max(out[others[k]], lv)
      }
    }
    if (total() > root.budget && named.indexOf(active) !== -1) out[active] = Math.max(out[active], 1)
    return out
  }

  // ------------------------------------------------------------------ icons
  readonly property var glyphs: ({
    git: String.fromCodePoint(0xE725), agent: "✦", chip: String.fromCodePoint(0xF4BC),
    ssh: String.fromCodePoint(0xF08C0), web: String.fromCodePoint(0xF059F), app: String.fromCodePoint(0xF08C6),
    pin: String.fromCodePoint(0xF0403), folder: String.fromCodePoint(0xF024B)
  })

  // An image URL for an icon spec, or "" to draw the glyph instead. Theme
  // lookups are remembered: they hit the disk, and the row asks often.
  property var iconCache: ({})
  function iconSource(icon, glyph) {
    if (root.iconStyle === "glyph" || !icon) return ""
    var key = icon.kind === "agent" ? String(icon.key)
            : icon.kind === "app" ? String(icon.name || "").replace(/^.*\//, "").replace(/\.(png|svg|xpm)$/i, "") : ""
    if (Object.prototype.hasOwnProperty.call(root.iconOverrides, key)) return root.iconOverrides[key]
    if (icon.kind === "agent") return Qt.resolvedUrl("assets/icons/" + icon.key + ".svg")
    if (icon.kind !== "app" || !icon.name) return ""
    var name = String(icon.name)
    if (name in root.iconCache) return root.iconCache[name]
    var url = name.charAt(0) === "/" ? "file://" + name : (Quickshell.iconPath(name, true) || "")
    root.iconCache[name] = url
    return url
  }
  function glyphFor(info) {
    var key = info && info.icon && info.icon.kind === "glyph" ? info.icon.key : (info ? info.glyph : "app")
    return root.glyphs[key] || root.glyphs.app
  }

  // ------------------------------------------------------------------ workspace panels
  property Item hoverCell: null
  property int hoverId: -1
  property Item pinCell: null
  property int pinId: -1
  readonly property bool pinOpen: root.pinId !== -1
  property bool projectsOpen: false
  property int recentRequestSerial: 0
  property int pendingRecentRequest: 0
  property string projectError: ""

  onNamesHiddenChanged: {
    if (root.namesHidden) {
      hoverDelay.stop()
      root.hoverId = -1
      root.close()
    }
  }

  function hoverEnter(cell, id) {
    root.pointerId = id
    hoverLeave.stop()
    if (!root.hoverOn || root.pinOpen || root.projectsOpen) return
    // With the engine down the card says why (and the bar shows plain numbers meanwhile).
    if (!root.downShown && (root.namesHidden || !root.names[String(id)])) return
    root.hoverCell = cell
    hoverDelay.pendingId = id
    if (root.hoverId !== -1) { root.hoverId = id; hoverCard.reanchor() } else hoverDelay.restart()
  }
  function hoverExit() { root.pointerId = -1; hoverDelay.stop(); hoverLeave.restart() }
  Timer { id: hoverDelay; interval: 300; property int pendingId: -1; onTriggered: { root.hoverId = pendingId; hoverCard.reanchor() } }
  Timer { id: hoverLeave; interval: 220; onTriggered: if (!hoverCard.containsMouse) root.hoverId = -1 }

  function openPin(cell, id) {
    if (root.namesHidden) return
    root.close()
    hoverDelay.stop()
    root.hoverId = -1
    root.pinCell = cell
    root.pinId = id
  }
  function close() {
    root.pinId = -1
    root.projectsOpen = false
    root.pendingRecentRequest = 0
    root.projectError = ""
  }

  function toggleProjects() {
    if (root.namesHidden) return "project switcher unavailable"
    if (root.projectsOpen) {
      root.close()
      return "closed"
    }
    var peers = root.barPeers()
    for (var i = 0; i < peers.length; i++) {
      if (peers[i] !== root && peers[i].projectsOpen) peers[i].close()
    }
    root.close()
    hoverDelay.stop()
    root.hoverId = -1
    root.projectsOpen = true
    return "opened"
  }

  function barPeers() {
    return root.bar && root.bar.moduleWidgets ? root.bar.moduleWidgets(root.moduleName) : [root]
  }
  // This widget on the focused monitor's bar (one Signpost per bar).
  function focusedPeer() {
    var focused = Hyprland.focusedMonitor
    return (focused && root.barPeers().find(function(widget) { return widget.monitorName === focused.name })) || root
  }

  function toggleProjectsOnFocusedMonitor() {
    var peers = root.barPeers()
    var target = root.focusedPeer()
    for (var i = 0; i < peers.length; i++) {
      if (peers[i] !== target && peers[i].projectsOpen) peers[i].close()
    }
    return target.toggleProjects()
  }

  function renameFocused() {
    var id = root.activeWorkspaceId()
    var cell = null
    for (var i = 0; i < cells.count; i++) {
      var item = cells.itemAt(i)
      if (item && item.modelData === id) cell = item
    }
    if (root.namesHidden) return "names are hidden right now"
    if (!cell || !root.names[String(id)]) return "nothing to name on this workspace yet"
    root.openPin(cell, id)
    return "opened"
  }

  function switchToProject(project) {
    if (root.namesHidden || !root.projectsOpen || root.pendingRecentRequest) return
    var target = root.projects.find(function(item) { return item.id === project.id })
    if (!target) return
    root.projectError = ""
    if (target.workspace > 0) {
      if (root.jumpToProject(target.workspace, target.address)) root.send({ cmd: "rememberProject", id: target.id })
      else root.projectError = "unavailable"
    } else {
      root.pendingRecentRequest = ++root.recentRequestSerial
      root.send({ cmd: "selectRecent", id: target.id, request: root.pendingRecentRequest })
    }
  }

  function jumpToProject(workspace, address) {
    if (!Number.isSafeInteger(workspace) || workspace <= 0) return false
    var addr = String(address || "")
    if (/^0x[0-9a-fA-F]+$/.test(addr)) {
      root.close()
      var selector = "\"" + workspace + "\""
      var winSel = "\"address:" + addr + "\""
      root.dispatch("(function() local w = hl.get_window(" + winSel + ") "
        + "if w then return hl.dsp.focus({ window = " + winSel + " }) end "
        + "local current = hl.get_active_workspace() "
        + "if (current and current.id == " + workspace + ") or not hl.get_workspace(" + selector + ") then "
        + "return hl.dsp.no_op() end return hl.dsp.focus({ workspace = " + selector + " }) end)()")
      return true
    }
    return root.jumpToProjectWorkspace(workspace)
  }

  function jumpToProjectWorkspace(workspace) {
    if (!Number.isSafeInteger(workspace) || workspace <= 0) return false
    root.close()
    // Check the workspace at dispatch time. The bar's cached focus may trail Hyprland.
    var selector = "\"" + workspace + "\""
    root.dispatch("(function() local current = hl.get_active_workspace() "
      + "if (current and current.id == " + workspace + ") or not hl.get_workspace(" + selector + ") then "
      + "return hl.dsp.no_op() end return hl.dsp.focus({ workspace = " + selector + " }) end)()")
    return true
  }

  function launchProjectTerminal(directory) {
    Quickshell.execDetached(["uwsm-app", "--", "xdg-terminal-exec", "--dir=" + directory])
  }

  function launchProjectEditor(directory) {
    Quickshell.execDetached(["omarchy-launch-editor", directory])
  }

  function splitCommand(text) {
    var out = [], cur = "", quote = "", escaped = false, s = String(text || "").trim()
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i)
      if (escaped) { cur += ch; escaped = false; continue }
      if (ch === "\\" && quote !== "'") { escaped = true; continue }
      if (quote) {
        if (ch === quote) quote = ""
        else cur += ch
      } else if (ch === "'" || ch === "\"") {
        quote = ch
      } else if (/\s/.test(ch)) {
        if (cur.length) { out.push(cur); cur = "" }
      } else {
        cur += ch
      }
    }
    if (cur.length) out.push(cur)
    return out
  }

  // The switcher's actions, on the selected project. Everything goes to the program as an
  // argument (never through a shell), and the remote is only ever an https address.
  function runProjectAction(kind, project) {
    if (!project || !project.directory) return
    if (kind === "terminal") root.launchProjectTerminal(project.directory)
    else if (kind === "editor") root.launchProjectEditor(project.directory)
    else if (kind === "agent") {
      var agent = root.splitCommand(root.agentCommand)
      if (agent.length === 0) return
      Quickshell.execDetached(["uwsm-app", "--", "xdg-terminal-exec", "--dir=" + project.directory].concat(agent))
    } else if (kind === "remote") {
      if (!/^https:\/\/[A-Za-z0-9.-]+\/[A-Za-z0-9._~\/-]+$/.test(project.remote || "")) return
      Quickshell.execDetached(["xdg-open", project.remote])
    } else return
    root.close()
  }

  // Git details cost a few milliseconds per project, so the engine only looks while the switcher is open.
  onProjectsOpenChanged: root.send({ cmd: "details", on: root.projectsOpen && !root.namesHidden })

  function handleRecentSelection(reply) {
    if (!root.pendingRecentRequest || reply.request !== root.pendingRecentRequest) return
    root.pendingRecentRequest = 0
    if (!root.projectsOpen || root.namesHidden) return
    if (reply.error) root.projectError = reply.error
    else if (reply.workspace > 0) {
      if (!root.jumpToProject(reply.workspace, reply.address)) root.projectError = "unavailable"
    } else if (reply.directory) {
      root.close()
      root.launchProjectEditor(reply.directory)
    }
  }

  function command(cmd, name) {
    root.send({ cmd: cmd, ws: root.pinId !== -1 ? root.pinId : root.activeWorkspaceId(), name: name || "" })
  }

  HoverCard {
    id: hoverCard
    owner: root
    bar: root.bar
    anchorItem: root.hoverCell || root
    widget: root
    info: root.hoverId !== -1 && !root.downShown ? (root.names[String(root.hoverId)] || null) : null
    wsLabel: root.hoverId !== -1 ? root.slotOf(root.hoverId) : 0
    status: root.hoverId !== -1 ? root.workspaceAgentStatus(root.hoverId) : ""
    commandStatus: root.hoverId !== -1 ? root.workspaceCommandStatus(root.hoverId) : ""
    age: info && info.agent === "wait" ? root.ageText(info.agentTs) : ""
    downReason: root.downShown ? root.downReason : ""
    hint: root.engineUp ? root.barHint : null
    onReplaceChosen: root.send({ cmd: "replace" })
    onKeepBothChosen: root.send({ cmd: "keepBoth" })
    colors: signColors
    open: root.hoverId !== -1 && (info !== null || root.downShown)
    onContainsMouseChanged: if (!containsMouse) hoverLeave.restart(); else hoverLeave.stop()
  }

  PinEditor {
    id: pinEditor
    widget: root
    anchorItem: root.pinCell || root
    owner: root
    bar: root.bar
    colors: signColors
    open: root.pinOpen
    info: root.pinOpen ? (root.names[String(root.pinId)] || null) : null
    wsLabel: root.pinOpen ? root.slotOf(root.pinId) : 0
  }

  ProjectSwitcher {
    owner: root
    bar: root.bar
    anchorItem: root
    open: root.projectsOpen && !root.namesHidden
    projects: open ? root.projects : []
    statusOf: root.projectStatus
    gitMode: root.switcherGit
    indexEnabled: root.indexEnabled
    ageOf: function(project) { return root.ageText(project.agentTs) }
    projectTone: function(project) { return root.toneFor(project) }
    keyLabel: function(workspace) { var slot = root.slotOf(workspace); return slot === root.slots ? "0" : String(slot) }
    colors: signColors
    onAction: function(kind, project) { root.runProjectAction(kind, project) }
    pending: root.pendingRecentRequest !== 0
    selectionError: root.projectError
    onQueryEdited: root.projectError = ""
    onChosen: function(project) { root.switchToProject(project) }
  }

  // ------------------------------------------------------------------ commands
  // omarchy-shell signpost explain | pin "name" | unpin | alias "name" | unalias | refresh
  function explainText() {
    var lines = []
    var ids = Object.keys(root.names).map(function(k) { return parseInt(k, 10) }).sort(function(a, b) { return a - b })
    for (var i = 0; i < ids.length; i++) {
      var r = root.names[String(ids[i])]
      var label = r.name + (r.more ? " +" + r.more : "")
      lines.push("ws " + String(ids[i]).padEnd(2) + " " + label.padEnd(18) + " " + String(r.source).padEnd(8) + " " + (r.how || ""))
      if (r.overridden) lines.push("      ↳ last used was " + r.overridden + " (weak name), so the strong name won")
      if (r.pending) lines.push("      ↳ you focused another window: → “" + r.pending.name + "” in " + Math.max(0, Math.round((r.pending.due - Date.now()) / 1000)) + " s unless you go back")
    }
    lines.push("")
    lines.push(root.engineUp ? ("probe " + root.probeMs.toFixed(1) + " ms · engine running") : ("engine down, showing numbers · " + root.downReason))
    if (root.claudeDetail) lines.push(root.claudeDetail)
    if (root.othersLine) lines.push(root.othersLine)
    if (root.barHint) lines.push("bar: Omarchy's workspace widget (" + root.barHint.id + ") is on the bar too; hover a workspace to replace it")
    return lines.join("\n")
  }

  function nextWaiting() {
    var best = null
    for (var key in root.names) {
      var w = root.names[key]
      if (w && w.agent === "wait" && (!best || Number(w.agentTs) < Number(best.agentTs))) best = w
    }
    if (!best) return "nothing is waiting for you"
    root.jumpToProjectWorkspace(best.id)
    var wsShown = root.blockOf(best.id) === root.block ? root.slotOf(best.id) : best.id
    return "workspace " + wsShown + " · " + best.name + " · waiting " + root.ageText(best.agentTs)
  }

  ShellIpc {
    target: "signpost"
    function explain(): string { return root.explainText() }
    function pin(name: string): string { root.command("pin", name); return "ok" }
    function unpin(): string { root.command("unpin"); return "ok" }
    function alias(name: string): string { root.command("alias", name); return "ok" }
    function unalias(): string { root.command("unalias"); return "ok" }
    function refresh(): string { root.send({ cmd: "refresh" }); return "ok" }
    function projects(): string { return root.toggleProjectsOnFocusedMonitor() }
    // The pin editor for the focused workspace, from the keyboard (the same as a right-click on it).
    function rename(): string { return root.focusedPeer().renameFocused() }
    function status(): string {
      if (!root.engineUp) return "engine down · " + root.downReason
      return "engine up · " + root.wsIds.length + " workspaces" + (root.claudeLine ? " · " + root.claudeLine : "")
             + (root.othersLine ? " · " + root.othersLine : "")
    }
    // Jump to the workspace whose agent has waited for you longest.
    function next(): string { return root.nextWaiting() }
  }

  // ------------------------------------------------------------------ the row
  // With the engine down for more than a few seconds, a small hollow ring after the numbers says the
  // names are missing on purpose; hovering any number says why. No banner: the bar keeps working.
  readonly property real downMark: root.downShown && !root.vertical ? Style.space(9) : 0
  readonly property real trailingGap: root.vertical ? 0 : Style.spaceReal(1.5) + root.downMark
  implicitWidth: grid.implicitWidth + trailingGap
  implicitHeight: grid.implicitHeight

  Rectangle {
    visible: root.downMark > 0
    anchors.verticalCenter: parent.verticalCenter
    anchors.right: parent.right
    anchors.rightMargin: Style.spaceReal(2)
    width: Style.space(6); height: width; radius: width / 2
    color: "transparent"
    border.width: 1.5
    border.color: Util.alpha(root.fg, 0.55)
    Accessible.name: "Signpost's engine stopped: showing plain numbers"
  }

  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.MiddleButton
    onClicked: root.toggleProjects()
  }

  GridLayout {
    id: grid
    anchors.fill: parent
    anchors.rightMargin: root.trailingGap
    columns: root.vertical ? 1 : root.wsIds.length
    columnSpacing: root.vertical ? 0 : Style.space(1)
    rowSpacing: root.vertical ? Style.space(2) : 0

    Repeater {
      id: cells
      model: root.wsIds

      Item {
        id: cell
        required property int modelData
        required property int index

        readonly property var workspace: root.workspaceById(modelData)
        readonly property bool occupied: workspace !== null && workspace.toplevels.values.length > 0
        readonly property bool shown: root.monitor !== null && root.monitor.activeWorkspace !== null && root.monitor.activeWorkspace.id === modelData
        readonly property var info: root.names[String(modelData)] || null
        readonly property int level: root.levels[modelData] !== undefined ? root.levels[modelData] : 3
        readonly property var parts: root.parts(modelData, level)
        readonly property bool named: parts.mark || parts.text
        readonly property bool planned: info !== null && !!info.planned
        readonly property bool firstGuest: root.blockOf(modelData) !== root.block && index > 0 && root.blockOf(root.wsIds[index - 1]) === root.block

        // For the bar's click routing (it owns left clicks so it can drag widgets).
        property bool interactive: true
        property bool pressable: true
        property int heldButtons: Qt.NoButton
        function triggerPress(button) {
          if (root.bar) root.bar.hideTooltip(cell)
          if (button === Qt.MiddleButton) { root.toggleProjects(); return }
          if (button === Qt.RightButton) { root.openPin(cell, modelData); return }
          if (shown) root.cycleLayout()
          else root.focusWorkspace(modelData)
        }
        property var registeredBar: null
        function syncClickRegistration() {
          if (registeredBar && registeredBar.unregisterClickTarget) registeredBar.unregisterClickTarget(cell)
          registeredBar = root.bar
          if (registeredBar && registeredBar.registerClickTarget) registeredBar.registerClickTarget(cell)
        }
        // Bars inject `bar` after creating the widget (Island Bar via Qt.callLater),
        // so register again whenever it arrives or changes, like WidgetButton does.
        readonly property var hostBar: root.bar
        onHostBarChanged: syncClickRegistration()
        Component.onCompleted: syncClickRegistration()
        Component.onDestruction: if (registeredBar && registeredBar.unregisterClickTarget) registeredBar.unregisterClickTarget(cell)

        Layout.leftMargin: firstGuest && !root.vertical ? Style.space(6) : 0
        Layout.topMargin: firstGuest && root.vertical ? Style.space(6) : 0
        Layout.preferredWidth: root.vertical ? root.barSize : Math.max(root.minCell, row.implicitWidth + root.cellPad * 2)
        Layout.preferredHeight: root.vertical ? Math.max(root.barSize, row.implicitHeight + root.cellPad) : root.barSize
        implicitWidth: Layout.preferredWidth
        implicitHeight: Layout.preferredHeight

        opacity: planned && !occupied ? 0.5 : (occupied || shown ? 1 : 0.5)
        Behavior on opacity { NumberAnimation { duration: Style.duration(140) } }

        property real pressScale: (cellMouse.pressedButtons | cell.heldButtons) ? 0.92 : 1
        Behavior on pressScale { NumberAnimation { duration: Style.duration(160); easing.type: Easing.OutCubic } }

        // A project's colour, or -1 for anything that isn't one (apps, ssh, models).
        readonly property int hue: info && info.hue !== undefined && info.hue !== null ? info.hue : -1
        readonly property color tone: root.toneFor(info)
        readonly property string activityState: root.workspaceStatus(modelData)
        readonly property string commandState: root.workspaceCommandStatus(modelData)
        readonly property color tileSurface: cell.pill ? Qt.tint(root.surface, cell.pillColor) : root.surface

        // The focused workspace sits in a pill: the text colour, tinted by the project's colour, with a
        // hairline edge, so it stands off the bar without shouting.
        readonly property bool pill: root.tiles && cell.shown && cell.named
        readonly property color pillBase: cell.hue >= 0 ? Qt.tint(root.fg, Util.alpha(cell.tone, 0.6)) : root.fg
        readonly property color pillColor: Util.alpha(cell.pillBase, 0.2)
        Rectangle {
          anchors.fill: parent
          anchors.topMargin: Style.spaceReal(3)
          anchors.bottomMargin: Style.spaceReal(3)
          visible: cell.pill
          radius: Math.min(height * 0.32, Style.cornerRadius)
          antialiasing: true
          color: cell.pillColor
          border.width: 1
          border.color: Util.alpha(cell.pillBase, 0.32)
        }

        Row {
          id: row
          anchors.centerIn: parent
          scale: cell.pressScale
          spacing: root.gap

          Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: cell.parts.number
            text: root.numberText(cell.modelData)
            width: root.textWidth(text)
            horizontalAlignment: Text.AlignHCenter
            textFormat: Text.PlainText
            color: root.tiles && !cell.shown ? Util.alpha(root.fg, 0.65) : root.fg
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.body
            renderType: Text.NativeRendering
          }
          SignIcon {
            anchors.verticalCenter: parent.verticalCenter
            widget: root
            visible: !root.tiles && cell.parts.mark
            spec: cell.info ? cell.info.icon : null
            glyphKey: cell.info && cell.info.icon && cell.info.icon.kind === "glyph" ? cell.info.icon.key : (cell.info ? cell.info.glyph : "app")
          }
          ProjectTile {
            visible: root.tiles && cell.parts.mark
            anchors.verticalCenter: parent.verticalCenter
            size: root.markSize
            reserveBadge: root.busyDot || root.commandStatus
            reduceMotion: Style.reduceMotion
            tinted: cell.hue >= 0
            tone: cell.tone
            ink: signColors.ink(cell.tone)
            monogram: cell.info && cell.info.monogram ? cell.info.monogram : ""
            status: cell.activityState
            // The badge's ring is cut from what is behind it: the pill on the focused workspace.
            ring: cell.tileSurface
            neutral: root.fg
            workColor: signColors.workColor
            waitColor: signColors.waitColor
            readyColor: signColors.readyColor
            failedColor: signColors.failedColor
            endedColor: signColors.endedColor
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

            contentItem: SignIcon {
              widget: root
              anchors.centerIn: parent
              size: parent.width
              spec: cell.info ? cell.info.icon : null
              glyphKey: cell.info && cell.info.icon && cell.info.icon.kind === "glyph" ? cell.info.icon.key : (cell.info ? cell.info.glyph : "app")
            }
          }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: cell.parts.text
            text: root.cellLabel(cell.info, cell.level)
            width: root.textWidth(text)
            textFormat: Text.PlainText   // window titles are app-controlled: never rich text
            // The focused name in full strength, the others a step back. Colour, not weight, so the
            // widths cellWidth() predicts stay the widths drawn.
            color: root.tiles && !cell.shown ? Util.alpha(root.fg, 0.8) : root.fg
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.body
            font.italic: cell.planned && !cell.occupied
            renderType: Text.NativeRendering
          }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: cell.parts.more
            text: cell.info ? "+" + cell.info.more : ""
            width: root.textWidth(text, true)
            textFormat: Text.PlainText
            color: Util.alpha ? Util.alpha(root.fg, 0.6) : root.fg
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.bodySmall
            renderType: Text.NativeRendering
          }
          Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            visible: cell.parts.unread
            width: root.unreadWidth(cell.info)
            height: Math.ceil(Style.font.bodySmall + Style.space(root.compact ? 1 : 3))
            radius: height / 2
            color: signColors.readable(Qt.tint(cell.tileSurface, Util.alpha(root.fg, 0.35)), cell.tileSurface, 3)
            Accessible.name: root.unreadLabel(cell.info) + " unread"

            Text {
              anchors.centerIn: parent
              text: root.unreadLabel(cell.info)
              textFormat: Text.PlainText
              color: signColors.readable(cell.tileSurface, parent.color, 4.5)
              font.family: fmSmall.font.family
              font.pixelSize: Style.font.bodySmall
              renderType: Text.NativeRendering
            }
          }
          Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            visible: cell.parts.dot
            width: Style.space(5); height: width; radius: width / 2
            readonly property color hue: signColors.readable(signColors.statusHue(cell.activityState), cell.tileSurface, 3)
            // ended: hollow, as on a tile, so it never reads as a pass.
            color: cell.activityState === "ended" ? "transparent" : hue
            border.width: cell.activityState === "ended" ? 1.5 : 0
            border.color: hue
            // A slow breath while it works; a still dot when the desktop asks for less motion.
            property real breath: 1
            opacity: Style.reduceMotion || (cell.activityState !== "work" && cell.activityState !== "wait") ? 1 : breath
            SequentialAnimation on breath {
              running: cell.parts.dot && (cell.activityState === "work" || cell.activityState === "wait") && !Style.reduceMotion
              loops: Animation.Infinite
              NumberAnimation { to: 0.4; duration: cell.activityState === "wait" ? 700 : 1200; easing.type: Easing.InOutSine }
              NumberAnimation { to: 1.0; duration: cell.activityState === "wait" ? 700 : 1200; easing.type: Easing.InOutSine }
            }
          }
        }

        MouseArea {
          id: cellMouse
          anchors.fill: parent
          // Bars that route clicks (to allow dragging widgets) call triggerPress and
          // consume the click; anywhere else, this MouseArea takes it directly.
          acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onEntered: root.hoverEnter(cell, cell.modelData)
          onExited: root.hoverExit()
          onClicked: function(mouse) { cell.triggerPress(mouse.button) }
          onWheel: function(wheel) { root.scrollWorkspaces(wheel.angleDelta.y) }
        }
      }
    }
  }
}
