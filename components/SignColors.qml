import QtQuick

// Signpost's colours, taken from the Omarchy theme's colors.toml so they follow the theme.
//
// Give it the file's text. Tile colours exist to tell projects apart, so they are picked for
// distance, not taken as they are:
//   1. each theme colour keeps its hue, but its lightness and chroma are brought into one band
//      (bright enough for a dark letter on a dark theme, deep enough for a light one), so a dull
//      or very dark theme colour still makes a clear tile;
//   2. from those, a greedy farthest-point pick in OKLab keeps only colours that are clearly
//      apart from each other and from the status colours (near-duplicates are dropped);
//   3. a theme with too few distinct hues (or none: a grey theme) is filled in with hues placed
//      in the largest gaps, at the theme's own lightness and chroma.
// Every tile's letter reads at 4.5:1 or better: the ink is whichever of near-black and near-white
// contrasts more, and a tile where neither does is nudged lighter or darker until one does.
//
// A project takes colour number (slot mod count). The engine hands out slots in the order projects
// are first seen and remembers them, so the first projects you open all differ and keep their colour.
// (Without a slot, the project's hue stands in.)
//
// Status: "working" is a blue, "needs you" amber, "ready" green, all taken from the theme where it
// has such a hue, more saturated than any tile, kept apart from every tile colour, and at least 3:1
// against the background they sit on. With no theme file it falls back to a dark theme with plain hues.
//
// Pure: nothing from the shell, so it can be tried on its own.
QtObject {
  id: palette

  property string text: ""
  readonly property var colors: palette.parse(palette.text)

  // Candidates, most characteristic first. Bright variants only add choice.
  readonly property var order: ["blue", "magenta", "green", "cyan", "orange", "red", "yellow",
                                "bright_blue", "bright_magenta", "bright_green", "bright_cyan", "bright_red", "bright_yellow"]

  // Distance (OKLab) below which two colours count as the same, and how many tiles to aim for.
  readonly property real apart: 0.068
  readonly property int fewest: 7
  readonly property int most: 9

  function parse(source) {
    var out = {}
    var lines = String(source || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var m = /^\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{6})"/.exec(lines[i])
      if (m) out[m[1]] = m[2]
      var mode = /^\s*mode\s*=\s*"(light|dark)"/.exec(lines[i])
      if (mode) out.mode = mode[1]
    }
    return out
  }

  // ---- OKLab (Björn Ottosson), so distances match what the eye sees
  function linear(v) { return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
  function encode(v) { return v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(v, 1 / 2.4) - 0.055 }

  function lab(color) {
    var c = Qt.color(color)
    var r = palette.linear(c.r), g = palette.linear(c.g), b = palette.linear(c.b)
    var l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    var m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    var s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    var L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    var A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    var B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    var h = Math.atan2(B, A) * 180 / Math.PI
    return { L: L, a: A, b: B, C: Math.sqrt(A * A + B * B), h: h < 0 ? h + 360 : h }
  }

  function rgbOf(L, C, h) {
    var a = C * Math.cos(h * Math.PI / 180), b = C * Math.sin(h * Math.PI / 180)
    var l = Math.pow(L + 0.3963377774 * a + 0.2158037573 * b, 3)
    var m = Math.pow(L - 0.1055613458 * a - 0.0638541728 * b, 3)
    var s = Math.pow(L - 0.0894841775 * a - 1.2914855480 * b, 3)
    return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s]
  }

  // An OKLCH colour, with chroma reduced until it fits sRGB (hue and lightness are kept).
  function lch(L, C, h) {
    var lo = 0, hi = C, rgb = palette.rgbOf(L, C, h)
    function fits(v) { return v[0] >= -0.0005 && v[0] <= 1.0005 && v[1] >= -0.0005 && v[1] <= 1.0005 && v[2] >= -0.0005 && v[2] <= 1.0005 }
    if (!fits(rgb)) {
      for (var i = 0; i < 18; i++) {
        var mid = (lo + hi) / 2
        if (fits(palette.rgbOf(L, mid, h))) lo = mid; else hi = mid
      }
      rgb = palette.rgbOf(L, lo, h)
    }
    function clamp(v) { return Math.max(0, Math.min(1, palette.encode(Math.max(0, v)))) }
    return Qt.rgba(clamp(rgb[0]), clamp(rgb[1]), clamp(rgb[2]), 1)
  }

  function distance(p, q) {
    var dl = p.L - q.L, da = p.a - q.a, db = p.b - q.b
    return Math.sqrt(dl * dl + da * da + db * db)
  }

  readonly property bool dark: {
    if (palette.colors.mode) return palette.colors.mode !== "light"
    return palette.colors.background ? palette.lab(palette.colors.background).L < 0.6 : true
  }

  // The band tiles live in: a dark letter reads on them in a dark theme, a light one in a light theme.
  // (Light themes: deep enough for white ink at 4.5:1, and well apart from the light background.)
  readonly property var band: palette.dark ? { low: 0.69, high: 0.82, chroma: 0.085, cap: 0.16 }
                                           : { low: 0.45, high: 0.52, chroma: 0.085, cap: 0.15 }

  readonly property color background: palette.colors.background ? Qt.color(palette.colors.background)
                                                                 : (palette.dark ? "#1a1b26" : "#f5f5f5")

  // ---- contrast (WCAG 2)
  function contrast(a, b) {
    var x = palette.luminance(a), y = palette.luminance(b)
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
  }

  // `color` moved in lightness (OKLCH, hue kept) just far enough to reach `min` against `against`.
  function readable(color, against, min) {
    if (palette.contrast(color, against) >= min) return Qt.color(color)
    var p = palette.lab(color)
    var up = palette.luminance(against) < 0.18
    for (var i = 1; i <= 40; i++) {
      var c = palette.lch(Math.max(0, Math.min(1, p.L + (up ? 0.015 : -0.015) * i)), p.C, p.h)
      if (palette.contrast(c, against) >= min) return c
    }
    return up ? Qt.rgba(1, 1, 1, 1) : Qt.rgba(0, 0, 0, 1)
  }

  // Secondary text: `ink` laid over `bg` at `alpha`, raised only as far as it takes to reach `min`.
  // Opaque, so what is measured is what is drawn.
  function legible(ink, bg, alpha, min) {
    var i = Qt.color(ink)
    for (var a = alpha; a < 1.0; a += 0.02) {
      var c = Qt.tint(bg, Qt.rgba(i.r, i.g, i.b, a))
      if (palette.contrast(c, bg) >= min) return c
    }
    return i
  }

  // A theme colour's hue, if it lies in [from, to] degrees (OKLCH); otherwise `fallback`.
  function hueNear(keys, from, to, fallback) {
    for (var i = 0; i < keys.length; i++) {
      var raw = palette.colors[keys[i]]
      if (!raw) continue
      var p = palette.lab(raw)
      if (p.C >= 0.04 && p.h >= from && p.h <= to) return p.h
    }
    return fallback
  }

  readonly property color waitColor: palette.readable(palette.lch(palette.dark ? 0.82 : 0.62, 0.16,
      palette.hueNear(["orange", "yellow", "bright_yellow"], 50, 95, 72)), palette.background, 3)
  readonly property color readyColor: palette.readable(palette.lch(palette.dark ? 0.80 : 0.58, 0.17,
      palette.hueNear(["green", "bright_green"], 125, 160, 145)), palette.background, 3)
  readonly property color workColor: palette.readable(palette.lch(palette.dark ? 0.74 : 0.55, 0.15,
      palette.hueNear(["blue", "bright_blue"], 225, 285, 258)), palette.background, 3)
  readonly property color addColor: palette.lch(palette.dark ? 0.78 : 0.55, 0.14,
                                                palette.hueNear(["green", "bright_green"], 110, 170, 145))
  readonly property color delColor: palette.lch(palette.dark ? 0.72 : 0.55, 0.15,
                                                palette.hueNear(["red", "bright_red"], 0, 40, 22))

  readonly property color failedColor: palette.readable(palette.delColor, palette.background, 3)
  // A command that finished with no known exit code: a quiet grey, so it never reads as passed.
  readonly property color endedColor: palette.readable(palette.lch(palette.dark ? 0.74 : 0.52, 0.02,
      palette.hueNear(["blue", "bright_blue"], 225, 285, 258)), palette.background, 3)

  function statusHue(state) {
    if (state === "work") return palette.workColor
    if (state === "wait") return palette.waitColor
    if (state === "ended") return palette.endedColor
    return state === "failed" ? palette.failedColor : palette.readyColor
  }

  // Every candidate, normalised into the band. Greys (no hue to keep) are left out.
  readonly property var candidates: {
    var out = [], seen = {}
    for (var i = 0; i < palette.order.length; i++) {
      var raw = palette.colors[palette.order[i]]
      if (!raw || seen[raw.toLowerCase()]) continue
      seen[raw.toLowerCase()] = true
      var p = palette.lab(raw)
      if (p.C < 0.025) continue
      var L = Math.max(palette.band.low, Math.min(palette.band.high, p.L))
      var C = Math.max(palette.band.chroma, Math.min(palette.band.cap, p.C))
      out.push(palette.lab(palette.lch(L, C, p.h)))
    }
    return out
  }

  // The tile colours, in the order they were picked: each is the farthest from those before it.
  readonly property var tones: {
    var seeds = [palette.lab(palette.waitColor), palette.lab(palette.readyColor), palette.lab(palette.workColor), palette.lab(palette.failedColor)]
    var chosen = []
    function nearest(p) {
      var best = 9
      for (var i = 0; i < seeds.length; i++) best = Math.min(best, palette.distance(p, seeds[i]))
      for (var j = 0; j < chosen.length; j++) best = Math.min(best, palette.distance(p, chosen[j]))
      return best
    }
    // Greedy farthest-point: always take the candidate farthest from everything taken so far.
    function pick(pool, limit) {
      var left = pool.slice()
      while (chosen.length < limit && left.length) {
        var at = -1, far = -1
        for (var i = 0; i < left.length; i++) {
          var d = nearest(left[i])
          if (d > far + 1e-9) { far = d; at = i }
        }
        if (far < palette.apart) break
        chosen.push(left[at])
        left.splice(at, 1)
      }
    }
    pick(palette.candidates, palette.most)

    if (chosen.length < palette.fewest) {
      // Fill the gaps at the theme's own lightness and chroma (or a quiet default for a grey theme).
      var Ls = chosen.map(function(p) { return p.L }).sort(function(x, y) { return x - y })
      var Cs = chosen.map(function(p) { return p.C }).sort(function(x, y) { return x - y })
      var L = Ls.length ? Ls[Math.floor(Ls.length / 2)] : (palette.band.low + palette.band.high) / 2
      var C = Cs.length ? Math.max(Cs[Math.floor(Cs.length / 2)], 0.09) : 0.09
      var ring = []
      for (var h = 0; h < 360; h += 10) ring.push(palette.lab(palette.lch(L, C, h)))
      pick(ring, palette.fewest)
    }

    return chosen.map(function(p) { return palette.inkable(palette.lch(p.L, p.C, p.h)) })
  }

  // A tile colour whose letter reaches 4.5:1 with one of the two inks: nudged away from the middle
  // greys (where neither black nor white reads) if it has to be.
  function inkable(color) {
    var best = function(c) { return Math.max(palette.contrast(c, palette.darkInk), palette.contrast(c, palette.lightInk)) }
    if (best(color) >= 4.5) return Qt.color(color)
    var p = palette.lab(color)
    for (var i = 1; i <= 30; i++) {
      var c = palette.lch(Math.max(0, Math.min(1, p.L + (palette.dark ? 0.01 : -0.01) * i)), p.C, p.h)
      if (best(c) >= 4.5) return c
    }
    return Qt.color(color)
  }

  // A project's colour for its slot (or, without one, its hue).
  function tone(index) {
    var n = palette.tones.length
    if (n === 0) return Qt.hsla((index % 360) / 360, 0.55, 0.62, 1)
    return palette.tones[Math.abs(Math.round(index)) % n]
  }

  // The tiles as [[tile, ink], …] in hex, for the engine to draw an alert's picture with.
  function hex(color) {
    function part(v) { var t = Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16); return t.length < 2 ? "0" + t : t }
    var c = Qt.color(color)
    return "#" + part(c.r) + part(c.g) + part(c.b)
  }
  readonly property var tilePairs: palette.tones.map(function(t) { return [palette.hex(t), palette.hex(palette.ink(t))] })

  // Ink for a letter on a tile of this colour: whichever of near-black and near-white reads better.
  function luminance(color) {
    var c = Qt.color(color)
    return 0.2126 * palette.linear(c.r) + 0.7152 * palette.linear(c.g) + 0.0722 * palette.linear(c.b)
  }
  readonly property color darkInk: Qt.rgba(0.06, 0.07, 0.1, 1)
  readonly property color lightInk: Qt.rgba(0.97, 0.97, 0.97, 1)
  function ink(color) {
    return palette.contrast(color, palette.darkInk) >= palette.contrast(color, palette.lightInk) ? palette.darkInk : palette.lightInk
  }
}
