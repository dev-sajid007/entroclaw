// End-to-end plan mode through the real agent server: read-only planning, todo list, then build.

import { afterAll, beforeAll, expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { createTestRenderer } from "@opentui/core/testing"

import { AgentClient } from "../src/api/agent.ts"
import { App } from "../src/app.ts"
import { startAgentServer, waitForText, type AgentServer } from "./support.ts"

const todos = (statuses: string[]) =>
  ["Read config.py", "Change the timeout", "Run tests"].map((content, i) => ({ content, status: statuses[i] }))

let server: AgentServer

beforeAll(async () => {
  server = await startAgentServer(
    [
      // Plan mode: reads, records a plan, and its attempted edit is refused by policy.
      {
        tool_calls: [
          { name: "read_file", args: { path: "config.py" } },
          { name: "update_todos", args: { todos: todos(["completed", "pending", "pending"]) } },
          { name: "edit_file", args: { path: "config.py", old_string: "10", new_string: "30" } },
        ],
      },
      { content: "Plan: raise TIMEOUT from 10 to 30, then run the tests." },
      // Build mode (/go): the edit now asks for approval.
      {
        tool_calls: [
          { name: "update_todos", args: { todos: todos(["completed", "in_progress", "pending"]) } },
          { name: "edit_file", args: { path: "config.py", old_string: "10", new_string: "30" } },
        ],
      },
      { content: "Done." },
    ],
    { "config.py": "TIMEOUT = 10\n" },
  )
}, 30_000)

afterAll(() => server?.stop())

test("plan mode, todos, then build", async () => {
  const setup = await createTestRenderer({ width: 100, height: 44 })
  const file = join(server.workspace, "config.py")
  try {
    const app = new App(setup.renderer, new AgentClient(server.url), () => {})
    await app.start()
    expect(app.store.state.sandbox).toMatch(/^(bwrap|off)/)

    await app.submit("/plan raise the timeout")
    expect(app.store.state.pendingApproval).toBeUndefined() // the edit was denied, not offered
    expect(readFileSync(file, "utf8")).toBe("TIMEOUT = 10\n")
    let frame = await waitForText(setup, "Plan: raise TIMEOUT")
    expect(frame).toContain(" PLAN ")
    expect(frame).toContain("Tasks 1/3")
    expect(frame).toContain("⛔ Tool edit_file")

    await app.submit("/go")
    expect(app.store.state.mode).toBe("build")
    expect(app.store.state.pendingApproval?.action).toBe("edit_file")
    await app.submit("y")
    expect(readFileSync(file, "utf8")).toBe("TIMEOUT = 30\n")
    frame = await waitForText(setup, "Done.")
    expect(frame).toContain("◐ Change the timeout")
    expect(frame).not.toContain(" PLAN ")
    app.dispose()
  } finally {
    setup.renderer.destroy()
  }
}, 30_000)
