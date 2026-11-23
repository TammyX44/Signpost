function isAlnum(ch) {
  return /[a-z0-9]/.test(ch)
}

function isBoundary(raw, lower, idx) {
  if (idx <= 0) return true
  if (!isAlnum(lower.charAt(idx - 1))) return true
  return raw.charAt(idx) !== lower.charAt(idx)
    && raw.charAt(idx - 1) === lower.charAt(idx - 1)
    && isAlnum(lower.charAt(idx - 1))
}

function isWordEnd(lower, end) {
  return end >= lower.length || !isAlnum(lower.charAt(end))
}

function scoreFuzzy(raw, lower, term) {
  var m = term.length
  var n = lower.length
  if (m < 2 || m > n) return 0

  var p = 0
  var greedy = []
  for (var i = 0; i < m; i++) {
    p = lower.indexOf(term.charAt(i), p)
    if (p < 0) return 0
    greedy.push(p)
    p++
  }

  var positions = greedy
  if (n <= 120 && m <= 32) {
    var NEG = -1e9
    var dp = new Array(m)
    var prev = new Array(m)
    for (var r = 0; r < m; r++) {
      dp[r] = new Array(n)
      prev[r] = new Array(n)
      for (var c = 0; c < n; c++) {
        dp[r][c] = NEG
        prev[r][c] = -1
      }
    }
    var ch0 = term.charAt(0)
    for (var j = 0; j < n; j++) {
      if (lower.charAt(j) !== ch0) continue
      dp[0][j] = (isBoundary(raw, lower, j) ? 18 : 0) + (j === 0 ? 12 : 0) - Math.min(j, 12)
    }
    for (var r2 = 1; r2 < m; r2++) {
      var ch = term.charAt(r2)
      var bestPrior = NEG
      var bestPriorIdx = -1
      for (var j2 = r2; j2 < n; j2++) {
        if (dp[r2 - 1][j2 - 1] > NEG / 2) {
          var cand = dp[r2 - 1][j2 - 1]
          if (cand > bestPrior) {
            bestPrior = cand
            bestPriorIdx = j2 - 1
          }
        }
        if (lower.charAt(j2) !== ch) continue
        var best = NEG
        var from = -1
        if (dp[r2 - 1][j2 - 1] > NEG / 2) {
          best = dp[r2 - 1][j2 - 1] + 14 + (isBoundary(raw, lower, j2) ? 16 : 0)
          from = j2 - 1
        }
        if (bestPriorIdx >= 0 && bestPriorIdx !== j2 - 1) {
          var gap = j2 - bestPriorIdx - 1
          var via = bestPrior + (isBoundary(raw, lower, j2) ? 18 : 0) - Math.min(gap, 8)
          if (via > best) {
            best = via
            from = bestPriorIdx
          }
        }
        if (best > NEG / 2) {
          dp[r2][j2] = best
          prev[r2][j2] = from
        }
      }
    }
    var endScore = NEG
    var endIdx = -1
    for (var k = m - 1; k < n; k++) {
      if (dp[m - 1][k] > endScore) {
        endScore = dp[m - 1][k]
        endIdx = k
      }
    }
    if (endIdx >= 0) {
      positions = new Array(m)
      var cur = endIdx
      for (var b = m - 1; b >= 0; b--) {
        positions[b] = cur
        cur = prev[b][cur]
      }
    }
  }

  var boundaries = 0
  var consecutive = 0
  for (var t = 0; t < m; t++) {
    if (isBoundary(raw, lower, positions[t])) boundaries++
    if (t > 0 && positions[t] === positions[t - 1] + 1) consecutive++
  }
  var firstAtBoundary = isBoundary(raw, lower, positions[0])
  var span = positions[m - 1] - positions[0] + 1
  var tightness = Math.round(40 * m / span)
  var base = firstAtBoundary ? 460 : 380
  var bonus = Math.min(80, boundaries * 18 + consecutive * 10) + tightness
  return Math.min(620, base + bonus)
}

function scoreText(rawTarget, term, allowFuzzy) {
  if (!rawTarget || !term) return 0
  var raw = String(rawTarget)
  var lower = raw.toLocaleLowerCase()
  if (lower.length !== raw.length) raw = lower
  if (lower === term) return 1000

  var idx = lower.indexOf(term)
  if (idx >= 0) {
    var tightness = Math.round(30 * term.length / lower.length)
    if (idx === 0) {
      return 820 + (isWordEnd(lower, term.length) ? 30 : 0) + tightness
    }
    var bestBoundary = 0
    var pos = idx
    while (pos >= 0) {
      if (isBoundary(raw, lower, pos)) {
        var score = 720 + (isWordEnd(lower, pos + term.length) ? 30 : 0) + tightness
        if (score > bestBoundary) bestBoundary = score
      }
      pos = lower.indexOf(term, pos + 1)
    }
    if (bestBoundary > 0) return bestBoundary
    return 200 + Math.round(40 * term.length / lower.length)
  }

  if (allowFuzzy === false) return 0
  return scoreFuzzy(raw, lower, term)
}

function groupOf(project) {
  if (!project) return "recent"
  if (project.workspace > 0) return "open"
  if (project.group === "projects") return "projects"
  return "recent"
}

function groupRank(project) {
  var g = groupOf(project)
  return g === "open" ? 0 : g === "recent" ? 1 : 2
}

function isWaitKeyword(term, allTerms) {
  if (term === "need" || term === "needs" || term === "wait" || term === "waiting") return true
  if (term === "you" && allTerms && (allTerms.indexOf("needs") >= 0 || allTerms.indexOf("need") >= 0)) return true
  return false
}

function bestTermScore(project, term, allTerms, status, keycap) {
  var best = 0
  var nameScore = scoreText(project.name, term, true)
  if (nameScore > best) best = nameScore

  if (project.originalName && project.originalName !== project.name) {
    var origScore = Math.round(scoreText(project.originalName, term, true) * 0.95)
    if (origScore > best) best = origScore
  }

  if (project.branch) {
    var branchScore = Math.round(scoreText(project.branch, term, true) * 0.85)
    if (branchScore > best) best = branchScore
  }

  var pathText = project.detail || project.directory || ""
  if (pathText) {
    var pathScore = Math.round(scoreText(pathText, term, false) * 0.75)
    if (pathScore > best) best = pathScore
  }

  if (keycap && term === keycap && 950 > best) best = 950

  if (status === "wait" && isWaitKeyword(term, allTerms) && 920 > best) best = 920

  return best
}

function scoreProject(project, query, statusOf, keyLabel) {
  var text = String(query || "").trim().toLocaleLowerCase()
  if (!text) return 0
  var terms = text.split(/\s+/)
  var status = statusOf ? (statusOf(project) || "") : ""
  var keycap = project.workspace > 0 && keyLabel ? String(keyLabel(project.workspace) || "").toLocaleLowerCase() : ""

  var total = 0
  for (var i = 0; i < terms.length; i++) {
    var s = bestTermScore(project, terms[i], terms, status, keycap)
    if (s <= 0) return 0
    total += s
  }
  if (terms.length > 1) {
    var whole = scoreText(project.name, text, false)
    if (project.originalName && project.originalName !== project.name) {
      whole = Math.max(whole, Math.round(scoreText(project.originalName, text, false) * 0.95))
    }
    if (whole > 0) total += Math.round(whole * 0.25)
  }
  return total
}

function filterProjects(projects, query, statusOf, keyLabel) {
  var list = Array.isArray(projects) ? projects : []
  var text = String(query || "").trim().toLocaleLowerCase()

  if (!text) {
    var waiting = [], open = [], recent = [], indexed = []
    for (var i = 0; i < list.length; i++) {
      var p = list[i]
      var g = groupOf(p)
      if (g === "open") {
        if (statusOf && statusOf(p) === "wait") waiting.push(p)
        else open.push(p)
      } else if (g === "recent") {
        recent.push(p)
      } else {
        indexed.push(p)
      }
    }
    waiting.sort(function(a, b) { return Number(a.agentTs || 0) - Number(b.agentTs || 0) })
    return waiting.concat(open, recent, indexed)
  }

  var scored = []
  for (var j = 0; j < list.length; j++) {
    var item = list[j]
    var score = scoreProject(item, text, statusOf, keyLabel)
    if (score > 0) {
      scored.push({
        project: item,
        score: score,
        waiting: statusOf ? statusOf(item) === "wait" : false,
        group: groupRank(item),
        index: j
      })
    }
  }
  scored.sort(function(a, b) {
    if (b.score !== a.score) return b.score - a.score
    if (a.waiting !== b.waiting) return a.waiting ? -1 : 1
    if (a.waiting && b.waiting) {
      var dt = Number(a.project.agentTs || 0) - Number(b.project.agentTs || 0)
      if (dt !== 0) return dt
    }
    if (a.group !== b.group) return a.group - b.group
    return a.index - b.index
  })
  return scored.map(function(entry) { return entry.project })
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    scoreText: scoreText,
    scoreProject: scoreProject,
    filterProjects: filterProjects,
    groupOf: groupOf
  }
}
