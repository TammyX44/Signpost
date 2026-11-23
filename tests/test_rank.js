const test = require("node:test")
const assert = require("node:assert/strict")
const Rank = require("../components/switcherRank.js")

const make = (overrides) => ({
  id: overrides.id || `${overrides.workspace || overrides.group || "recent"}:${overrides.name}`,
  name: "Project",
  originalName: overrides.originalName || overrides.name || "Project",
  detail: overrides.detail || `~/Projects/${overrides.name || "Project"}`,
  directory: overrides.directory || `/home/u/Projects/${overrides.name || "Project"}`,
  workspace: 0,
  group: overrides.workspace > 0 ? "open" : "recent",
  branch: "",
  agentTs: 0,
  ...overrides,
})

const slotKeyLabel = (ws) => {
  const slot = ((ws - 1) % 10) + 1
  return slot === 10 ? "0" : String(slot)
}

test("arc ranks Arc · docs above a row whose only match is search", () => {
  const rows = [
    make({ name: "search", workspace: 1, detail: "~/Projects/search" }),
    make({ name: "Web · search", workspace: 2, detail: "~/Projects/web-search" }),
    make({ name: "Arc · docs", workspace: 0, group: "recent", detail: "~/Projects/Arc-wt-docs" }),
  ]
  const out = Rank.filterProjects(rows, "arc", () => "", slotKeyLabel)
  assert.equal(out[0].name, "Arc · docs")
  assert.equal(out.length, 3)
})

test("scoring hierarchy: exact > word-start/prefix > fuzzy subsequence > interior substring", () => {
  const exact = Rank.scoreText("arc", "arc", true)
  const prefix = Rank.scoreText("Arc · docs", "arc", true)
  const wordStart = Rank.scoreText("my-arc-tool", "arc", true)
  const fuzzy = Rank.scoreText("a-r-c", "arc", true)
  const substr = Rank.scoreText("search", "arc", true)

  assert.ok(exact > prefix, `${exact} > ${prefix}`)
  assert.ok(prefix > wordStart, `${prefix} > ${wordStart}`)
  assert.ok(wordStart > fuzzy, `${wordStart} > ${fuzzy}`)
  assert.ok(fuzzy > substr, `${fuzzy} > ${substr}`)
  assert.ok(substr > 0)
})

test("sgnp finds Signpost via fuzzy subsequence", () => {
  const rows = [
    make({ name: "Arc · docs", workspace: 1 }),
    make({ name: "Signpost", workspace: 0, group: "projects", detail: "~/Projects/Signpost" }),
    make({ name: "Radar", workspace: 2 }),
  ]
  const out = Rank.filterProjects(rows, "sgnp", () => "", slotKeyLabel)
  assert.deepEqual(out.map((r) => r.name), ["Signpost"])
})

test("matches alias/original name, folder path, and branch", () => {
  const rows = [
    make({ name: "Client Portal", originalName: "Lumen", detail: "~/work/lumen-web", branch: "feat/oauth" }),
    make({ name: "Radar", detail: "~/Projects/Radar", branch: "main" }),
  ]
  assert.equal(Rank.filterProjects(rows, "lumen", () => "", slotKeyLabel)[0].name, "Client Portal")
  assert.equal(Rank.filterProjects(rows, "oauth", () => "", slotKeyLabel)[0].name, "Client Portal")
  assert.equal(Rank.filterProjects(rows, "lumen-web", () => "", slotKeyLabel)[0].name, "Client Portal")
})

test("searches keycap slot number instead of raw Hyprland workspace id", () => {
  const rows = [
    make({ name: "Radar", workspace: 12, detail: "~/Projects/Radar" }),
    make({ name: "Lumen", workspace: 20, detail: "~/Projects/Lumen" }),
    make({ name: "Arc", workspace: 1, detail: "~/Projects/Arc" }),
  ]
  const byTwo = Rank.filterProjects(rows, "2", () => "", slotKeyLabel)
  assert.deepEqual(byTwo.map((r) => r.name), ["Radar"])

  const byTwelve = Rank.filterProjects(rows, "12", () => "", slotKeyLabel)
  assert.deepEqual(byTwelve, [])

  const byZero = Rank.filterProjects(rows, "0", () => "", slotKeyLabel)
  assert.deepEqual(byZero.map((r) => r.name), ["Lumen"])
})

test("strong recent or indexed hit beats weak open hit once a query is typed", () => {
  const rows = [
    make({ name: "Radar", workspace: 1, detail: "~/Projects/Radar" }),
    make({ name: "Arc · docs", workspace: 0, group: "recent", detail: "~/Projects/Arc-wt-docs" }),
    make({ name: "Archive", workspace: 0, group: "projects", detail: "~/Projects/Archive" }),
  ]
  const out = Rank.filterProjects(rows, "ar", () => "", slotKeyLabel)
  assert.equal(out[out.length - 1].name, "Radar")
  assert.ok(["Arc · docs", "Archive"].includes(out[0].name))
})

test("empty query keeps needs-you first (longest wait on top), then open, recent, projects", () => {
  const rows = [
    make({ id: "p1", name: "Zeta", workspace: 0, group: "projects" }),
    make({ id: "r1", name: "Beta", workspace: 0, group: "recent" }),
    make({ id: "o1", name: "OpenIdle", workspace: 1 }),
    make({ id: "w2", name: "WaitNewer", workspace: 2, agentTs: 200 }),
    make({ id: "w1", name: "WaitOlder", workspace: 3, agentTs: 100 }),
  ]
  const statusOf = (p) => (p.id.startsWith("w") ? "wait" : "")
  const out = Rank.filterProjects(rows, "", statusOf, slotKeyLabel)
  assert.deepEqual(out.map((r) => r.name), ["WaitOlder", "WaitNewer", "OpenIdle", "Beta", "Zeta"])
})

test("typing needs or wait matches rows that need you", () => {
  const rows = [
    make({ id: "o1", name: "Radar", workspace: 1 }),
    make({ id: "w2", name: "Lumen", workspace: 2, agentTs: 200 }),
    make({ id: "w1", name: "Arc · docs", workspace: 3, agentTs: 100 }),
  ]
  const statusOf = (p) => (p.id.startsWith("w") ? "wait" : "")
  assert.deepEqual(
    Rank.filterProjects(rows, "needs", statusOf, slotKeyLabel).map((r) => r.name),
    ["Arc · docs", "Lumen"]
  )
  assert.deepEqual(
    Rank.filterProjects(rows, "wait", statusOf, slotKeyLabel).map((r) => r.name),
    ["Arc · docs", "Lumen"]
  )
})
