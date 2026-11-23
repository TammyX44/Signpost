import QtQuick

// Marks the part of a name that matches a search, as StyledText.
//
// Names come from window titles and folder names, so they are app-controlled: every character
// is escaped first, and the only tags in the result are the ones added here.
QtObject {
  function safe(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  }

  function hex(color) {
    function part(v) { var s = Math.round(v * 255).toString(16); return s.length < 2 ? "0" + s : s }
    var c = Qt.color(color)   // a colour value or a string like "#e0af68"
    return "#" + part(c.r) + part(c.g) + part(c.b)
  }

  // Merged [start, end) ranges of `text` that contain any of the lower-case `terms`.
  function ranges(text, terms) {
    var lower = text.toLowerCase()
    if (lower.length !== text.length) return []   // a few letters change length when lowered: skip rather than mis-mark
    var found = []
    for (var i = 0; i < terms.length; i++) {
      var term = terms[i]
      if (!term) continue
      var at = lower.indexOf(term)
      if (at !== -1) {
        while (at !== -1) {
          found.push([at, at + term.length])
          at = lower.indexOf(term, at + term.length)
        }
      } else if (term.length >= 2) {
        var pos = 0, sub = []
        for (var c = 0; c < term.length; c++) {
          pos = lower.indexOf(term.charAt(c), pos)
          if (pos === -1) { sub = []; break }
          sub.push([pos, pos + 1])
          pos++
        }
        for (var s = 0; s < sub.length; s++) found.push(sub[s])
      }
    }
    found.sort(function(a, b) { return a[0] - b[0] })
    var merged = []
    for (var k = 0; k < found.length; k++) {
      var last = merged[merged.length - 1]
      if (last && found[k][0] <= last[1]) last[1] = Math.max(last[1], found[k][1])
      else merged.push([found[k][0], found[k][1]])
    }
    return merged
  }

  // `mark` colours the matches; `muted` colours the " · " in "repo · worktree".
  function markup(text, terms, mark, muted) {
    text = String(text)
    var spans = ranges(text, terms)
    var out = ""
    var from = 0
    for (var i = 0; i < spans.length; i++) {
      out += safe(text.slice(from, spans[i][0]))
      out += "<b><font color=\"" + hex(mark) + "\">" + safe(text.slice(spans[i][0], spans[i][1])) + "</font></b>"
      from = spans[i][1]
    }
    out += safe(text.slice(from))
    return out.replace(" · ", "<font color=\"" + hex(muted) + "\"> · </font>")
  }
}
