// End-to-end for always-allow, undo, sessions and multi-line input through the real agent server.

import { afterAll, beforeAll, expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { createTestRenderer } from "@opentui/core/testing"

import { AgentClient } from "../src/api/agent.ts"
import { App } from "../src/app.ts"
import { startAgentServer, waitForText, type AgentServer } from "./support.ts"

const edit = (from: string, to: string) => ({
  tool_calls: [{ name: "edit_file", args: { path: "app.py", old_string: from, new_string: to } }],
})

let server: AgentServer

beforeAll(async () => {
  server = await startAgentServer(
    [
      // Turn 1: two edits; the user allows edit_file "always", so the second one doesn't ask.
      edit("v1", "v2"),
      edit("v2", "v3"),
      { content: "Bumped to **v3**." },
      // Turn 2 (after /undo): a plain answer.
      { content: "Noted." },
    ],
    { "app.py": "VERSION = 'v1'\n" },
  )
}, 30_000)

afterAll(() => server?.stop())

test("always-allow, undo, sessions", async () => {
  const setup = await createTestRenderer({ width: 100, height: 40 })
  const file = join(server.workspace, "app.py")
  try {
    const app = new App(setup.renderer, new AgentClient(server.url), () => {})
    await app.start()

    await app.submit("bump the version")
    expect(app.store.state.pendingApproval?.allow_always).toBe(true)
    await app.submit("a")
    expect(app.store.state.pendingApproval).toBeUndefined() // the second edit ran under the rule
    expect(readFileSync(file, "utf8")).toBe("VERSION = 'v3'\n")
    await waitForText(setup, "Bumped to v3.")

    await app.submit("/rules")
    await waitForText(setup, "Always allowed this session:")

    await app.submit("/undo")
    await waitForText(setup, "Undo: restored app.py")
    expect(readFileSync(file, "utf8")).toBe("VERSION = 'v1'\n")

    // The model is told about the undo in the next turn's history.
    const session = await new AgentClient(server.url).getSession(app.store.state.sessionId!)
    expect(session.messages.at(-1)?.content).toContain("undid the agent's changes to: app.py")

    // Multi-line input goes through as one message.
    await setup.mockInput.typeText("first line")
    setup.mockInput.pressKey("LINEFEED")
    await setup.mockInput.typeText("second line")
    setup.mockInput.pressEnter()
    await waitForText(setup, "Noted.")

    const sessionId = app.store.state.sessionId
    await app.submit("/new")
    await app.submit("/sessions")
    await waitForText(setup, "bump the version")
    await app.submit("/resume 1")
    expect(app.store.state.sessionId).toBe(sessionId)
    const frame = await waitForText(setup, "second line")
    expect(frame).toContain("first line")
    app.dispose()
  } finally {
    setup.renderer.destroy()
  }
}, 30_000)
