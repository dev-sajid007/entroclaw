// End-to-end: OpenTUI app → HTTP/SSE → real Python agent server → LangGraph → tools → workspace.
// The LLM is replaced by a scripted model (FAKE_MODEL_SCRIPT) so this runs without an API key.

import { afterAll, beforeAll, expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { createTestRenderer } from "@opentui/core/testing"

import { AgentClient } from "../src/api/agent.ts"
import { App } from "../src/app.ts"
import { startAgentServer, waitForText, type AgentServer } from "./support.ts"

let server: AgentServer
let URL = ""
let workspace = ""

beforeAll(async () => {
  server = await startAgentServer(
    [
      { tool_calls: [{ name: "read_file", args: { path: "login.py" } }] },
      {
        content: "The condition is inverted.",
        tool_calls: [{ name: "edit_file", args: { path: "login.py", old_string: "is None", new_string: "is not None" } }],
      },
      { tool_calls: [{ name: "run_command", args: { command: "cat login.py" } }] },
      { content: "Fixed the login check." },
    ],
    { "login.py": "def login(user):\n    return user is None\n" },
  )
  URL = server.url
  workspace = server.workspace
}, 30_000)

afterAll(() => server?.stop())

test("fix a bug through the full stack with approval", async () => {
  const setup = await createTestRenderer({ width: 100, height: 40 })
  try {
    const app = new App(setup.renderer, new AgentClient(URL), () => {})
    await app.start()
    expect(app.store.state.connection).toBe("connected")
    expect(app.store.state.workspace).toBe(workspace)

    await app.submit("Fix the login bug")
    expect(app.store.state.pendingApproval?.action).toBe("edit_file")
    await setup.waitForVisualIdle()
    let frame = setup.captureCharFrame()
    expect(frame).toContain('read_file("login.py")')
    expect(frame).toContain("Approval Required")
    expect(frame).toContain("+    return user is not None")
    expect(readFileSync(join(workspace, "login.py"), "utf8")).toContain("is None")

    await app.submit("y")
    // `cat login.py` is on the read-only allowlist, so it runs without a second approval.
    expect(app.store.state.pendingApproval).toBeUndefined()
    expect(readFileSync(join(workspace, "login.py"), "utf8")).toContain("is not None")
    frame = await waitForText(setup, "Fixed the login check.")
    expect(frame).toContain("✓ Allowed")
    expect(frame).toContain('run_command("cat login.py")')
    expect(frame).toContain("Fixed the login check.")
    expect(frame).toContain("Ready")

    // The server keeps the session; a new client can resume it from the checkpoint.
    const session = await new AgentClient(URL).getSession(app.store.state.sessionId!)
    expect(session.messages.at(-1)).toMatchObject({ role: "agent", content: "Fixed the login check." })
    app.dispose()
  } finally {
    setup.renderer.destroy()
  }
}, 30_000)
