// Signpost OpenCode 2.x adapter
import { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"

const script = "__SIGNPOST_SCRIPT__"
const types = ["permission.asked", "permission.replied", "session.execution.started",
  "session.execution.succeeded", "session.execution.failed", "session.execution.interrupted", "session.deleted"]

export default Plugin.define({
  id: "signpost",
  setup(ctx) {
    const controller = new AbortController()
    const seen = new Set()
    const pending = new Map()
    const cwds = new Map()
    void (async () => {
      for await (const event of ctx.event.subscribe({ types, signal: controller.signal })) {
        const { id, type, data, location } = event
        if (!id || seen.has(id)) continue
        seen.add(id)
        if (seen.size > 1024) seen.delete(seen.values().next().value)
        const session = data?.sessionID
        if (!session) continue
        if (location?.directory) cwds.set(session, location.directory)
        const cwd = location?.directory || cwds.get(session)
        if (!cwd && type !== "session.deleted") continue
        const requests = pending.get(session) ?? new Map()
        let eventType = type
        let message = ""
        let replaceMessage = false
        if (type === "permission.asked") {
          if (!data.id || requests.has(data.id)) continue
          message = data.action ? `Allow ${data.action}?` : ""
          requests.set(data.id, message)
          pending.set(session, requests)
        } else if (type === "permission.replied") {
          requests.delete(data.requestID)
          if (requests.size) {
            eventType = "permission.asked"
            message = requests.values().next().value || ""
            replaceMessage = true
          } else {
            pending.delete(session)
          }
        } else if (type === "session.execution.started") {
          if (requests.size) continue
        } else {
          pending.delete(session)
          if (type === "session.deleted") cwds.delete(session)
        }
        await new Promise(resolve => {
          const child = spawn("python3", ["-I", script, "hook", "--agent", "opencode"], {
            stdio: ["pipe", "ignore", "ignore"], timeout: 1000, signal: controller.signal
          })
          child.once("error", resolve)
          child.once("close", resolve)
          child.stdin.on("error", () => {})
          child.stdin.end(JSON.stringify({ hook_event_name: eventType, session_id: session, cwd: cwd || "",
            message, replace_message: replaceMessage }))
        })
      }
    })().catch(() => {})
    return () => controller.abort()
  }
})
