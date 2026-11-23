import QtQuick
import QtQuick.Shapes

// A workspace's tile: a project's letter on its colour, or an app's logo on a neutral tile, with a
// small status badge on its corner.
//
//   tinted     a project of yours: the letter on a tile of `tone`
//   not tinted anything else (an app, an ssh host, a local model): a neutral tile that
//              holds the logo you give it as `contentItem`
//   outlined   a project that isn't open (recent projects): outline only, no fill
//   status     "" | "work" (spinner) | "wait" (pulsing: it needs you) | "ready" (dot) | "failed" (reported command exit)
//              | "ended" (hollow grey ring: a command finished, its exit code unknown)
//   reduceMotion  the desktop asks for less motion (Omarchy's Style.reduceMotion): every state is
//              drawn still. The still frames read on their own: an arc on a faint ring, a dot.
//
// It takes everything as properties and imports nothing from the shell, so it can be
// drawn on its own. The bar and the switcher pass in the theme's colours.
Item {
  id: tile

  property real size: 18
  property bool tinted: false
  property color tone: "#7aa2f7"
  property color ink: "#10111a"
  property string monogram: ""
  property string status: ""
  property bool outlined: false
  property color ring: "#10111a"        // the surface behind the tile, so the badge can cut into it
  property color neutral: "#ffffff"     // the theme's text colour, for neutral tiles
  property string fontFamily: ""
  property color workColor: "#7aa2f7"
  property color waitColor: "#e0af68"
  property color failedColor: "#f7768e"
  property color readyColor: "#9ece6a"
  property color endedColor: "#a9b1d6"
  property alias contentItem: content.data
  property bool reduceMotion: false

  // A new question pings every 2.5 s for its first 30 s, then settles to one ping every 10 s: it
  // stays findable without nagging while you finish what you were doing.
  property bool settled: false
  onStatusChanged: { tile.settled = false; settleTimer.restart() }
  Timer { id: settleTimer; interval: 30000; running: tile.status === "wait"; onTriggered: tile.settled = true }
  onSettledChanged: if (pingAnim.running) pingAnim.restart()

  readonly property real corner: size * 0.3
  // Room to keep free on the right for the badge. Off by default: the switcher and the hover card
  // have room already; the bar reserves it so the badge never touches the name that follows.
  property bool reserveBadge: false

  // The badge is sized for legibility, not as a fixed share of the tile: about 8 px on the bar's
  // 21 px tile, 11 px on the switcher's 32 px one. It sits on the corner's curve and leans out, so it
  // covers as little of the letter or logo as it can.
  readonly property real badgeSize: Math.round(size * 0.26 + 2.5)
  readonly property real ringWidth: size < 26 ? 1.5 : 2
  readonly property real badgeCenter: size - Math.max(1.5, size * 0.06)
  readonly property real overhang: Math.ceil(badgeCenter + badgeSize / 2 + ringWidth - size)

  implicitWidth: size + (reserveBadge ? overhang : 0)
  implicitHeight: size

  // ---- contrast: the badge touches only its ring, so each state colour must read on the ring.
  function luminance(color) {
    function lin(v) { return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
    var c = Qt.color(color)
    return 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b)
  }
  function contrast(a, b) {
    var x = tile.luminance(a), y = tile.luminance(b)
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
  }
  // `color`, made lighter or darker (hue kept) until it reaches 3:1 on `under`.
  function onRing(color, under) {
    var c = Qt.color(color)
    if (tile.contrast(c, under) >= 3) return c
    var up = tile.luminance(under) < 0.18
    for (var i = 1; i <= 20; i++) {
      var next = up ? Qt.lighter(c, 1 + 0.06 * i) : Qt.darker(c, 1 + 0.06 * i)
      if (tile.contrast(next, under) >= 3) return next
    }
    return up ? Qt.rgba(1, 1, 1, 1) : Qt.rgba(0, 0, 0, 1)
  }
  readonly property color workShown: tile.onRing(tile.workColor, tile.ring)
  readonly property color waitShown: tile.onRing(tile.waitColor, tile.ring)
  readonly property color failedShown: tile.onRing(tile.failedColor, tile.ring)
  readonly property color readyShown: tile.onRing(tile.readyColor, tile.ring)
  readonly property color endedShown: tile.onRing(tile.endedColor, tile.ring)
  // A light theme (dark text): neutral tiles need more body to stand off a light background.
  readonly property bool lightTheme: tile.luminance(tile.neutral) < 0.2

  Rectangle {
    id: face
    width: tile.size
    height: tile.size
    radius: tile.corner
    antialiasing: true
    border.width: tile.outlined && tile.tinted ? 1.5 : (tile.tinted ? 0 : 1)
    border.color: tile.tinted ? Qt.rgba(tile.tone.r, tile.tone.g, tile.tone.b, 0.7)
                              : Qt.rgba(tile.neutral.r, tile.neutral.g, tile.neutral.b, tile.lightTheme ? 0.3 : 0.13)
    color: tile.tinted ? (tile.outlined ? "transparent" : tile.tone)
                       : Qt.rgba(tile.neutral.r, tile.neutral.g, tile.neutral.b, 0.14)
    gradient: tile.tinted ? (tile.outlined ? null : sheen) : glass

    // A neutral tile (an app, a host, a model) is lit like a coloured one, top to bottom, with a
    // hairline edge: a solid object of its own, never a faded or disabled-looking coloured tile.
    Gradient {
      id: glass
      GradientStop { position: 0; color: Qt.rgba(tile.neutral.r, tile.neutral.g, tile.neutral.b, tile.lightTheme ? 0.10 : 0.17) }
      GradientStop { position: 1; color: Qt.rgba(tile.neutral.r, tile.neutral.g, tile.neutral.b, tile.lightTheme ? 0.17 : 0.09) }
    }

    Gradient {
      id: sheen
      GradientStop { position: 0; color: Qt.lighter(tile.tone, 1.14) }
      GradientStop { position: 1; color: Qt.darker(tile.tone, 1.1) }
    }

    Text {
      anchors.centerIn: parent
      visible: tile.tinted
      text: tile.monogram
      textFormat: Text.PlainText
      color: tile.outlined ? tile.tone : tile.ink
      font.family: tile.fontFamily
      font.pixelSize: Math.round(tile.size * 0.58)
      font.weight: Font.ExtraBold
      renderType: Text.NativeRendering
    }

    Item {
      id: content
      anchors.centerIn: parent
      width: tile.size * 0.7
      height: width
      visible: !tile.tinted
    }
  }

  // Status badge. Drawn over the corner, with a ring in the surface colour around it.
  Item {
    id: badge
    visible: tile.status !== ""
    width: tile.badgeSize
    height: width
    x: tile.badgeCenter - width / 2
    y: tile.badgeCenter - height / 2

    Rectangle {
      anchors.fill: parent
      anchors.margins: -tile.ringWidth
      radius: width / 2
      antialiasing: true
      color: tile.ring
    }

    // ready: a plain dot. wait: a dot that sends out rings, so it reads as "look here". ended: a hollow
    // ring, so "it stopped" never looks like "it passed", even without colour.
    Rectangle {
      id: dot
      anchors.fill: parent
      visible: tile.status === "ready" || tile.status === "wait" || tile.status === "failed" || tile.status === "ended"
      radius: width / 2
      antialiasing: true
      color: tile.status === "ended" ? "transparent"
           : tile.status === "wait" ? tile.waitShown : (tile.status === "failed" ? tile.failedShown : tile.readyShown)
      border.width: tile.status === "ended" ? (tile.size < 26 ? 1.75 : 2.2) : 0
      border.color: tile.endedShown
    }

    Rectangle {
      id: ping
      anchors.centerIn: parent
      width: badge.width
      height: width
      radius: width / 2
      visible: tile.status === "wait"
      color: "transparent"
      border.width: 1.5
      border.color: tile.waitShown
      opacity: 0

      SequentialAnimation {
        id: pingAnim
        running: ping.visible && !tile.reduceMotion
        loops: Animation.Infinite
        ParallelAnimation {
          ScaleAnimator { target: ping; from: 0.8; to: 1.9; duration: 1200; easing.type: Easing.OutQuart }
          OpacityAnimator { target: ping; from: 0.75; to: 0; duration: 1200; easing.type: Easing.OutQuad }
        }
        PauseAnimation { duration: tile.settled ? 8800 : 1300 }
      }
    }

    // working: a full faint track with a thick arc on it, so it reads as "in progress" even in a still.
    Rectangle {
      anchors.fill: parent
      visible: tile.status === "work"
      radius: width / 2
      antialiasing: true
      color: "transparent"
      border.width: tile.size < 26 ? 2 : 2.4
      border.color: Qt.rgba(tile.workShown.r, tile.workShown.g, tile.workShown.b, 0.3)
    }

    Shape {
      id: spinner
      anchors.fill: parent
      visible: tile.status === "work"
      preferredRendererType: Shape.CurveRenderer

      ShapePath {
        strokeColor: tile.workShown
        strokeWidth: tile.size < 26 ? 2 : 2.4
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        PathAngleArc {
          centerX: badge.width / 2
          centerY: badge.height / 2
          radiusX: (badge.width - (tile.size < 26 ? 2 : 2.4)) / 2
          radiusY: (badge.height - (tile.size < 26 ? 2 : 2.4)) / 2
          startAngle: -90
          sweepAngle: 130
        }
      }

      // Rotation runs on the render thread; it costs the bar nothing while it spins. One unhurried
      // turn every 1.6 s: alive, not anxious. Still (the arc at the top) with reduced motion.
      RotationAnimator on rotation {
        id: spin
        from: 0
        to: 360
        duration: 1600
        loops: Animation.Infinite
        running: spinner.visible && !tile.reduceMotion
        onRunningChanged: if (!running) spinner.rotation = 0
      }
    }
  }
}
