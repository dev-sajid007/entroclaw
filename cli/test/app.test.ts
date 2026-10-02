import { afterEach, expect, test } from "bun:test"
import { createTestRenderer, type TestRendererSetup } from "@opentui/core/testing"

import type { AgentClient } from "../src/api/agent.ts"
import type { AgentEvent } from "../src/api/events.ts"
import { App } from "../src/app.ts"
import { waitForText } from "./support.ts"

type Script = AgentEvent[][]

/** In-memory stand-in for the HTTP client: each send/approval call plays the next scripted run. */
function fakeClient(script: Script) {
  const calls: { kind: string; args: unknown[] }[] = []
  let next = 0
  async function* play(): AsyncGenerator<AgentEvent> {
    for (const event of script[next++] ?? []) yield event
  }
  const client = {
    calls,
    health: async () => ({ status: "ok", workspace: "/work/project", model: "test-model", require_approval: true, tools: [], mcp_errors: {} }),
    createSession: async () => "session-123456789",
    getSession: async () => ({ session_id: "s", messages: [], pending_approval: null }),
    sendMessage: (...args: unknown[]) => (calls.push({ kind: "message", args }), play()),
    respondApproval: (...args: unknown[]) => (calls.push({ kind: "approval", args }), play()),
  }
  return client
}

let setup: TestRendererSetup | undefined

afterEach(() => {
  setup?.renderer.destroy()
  setup = undefined
})

async function mount(script: Script) {
  setup = await createTestRenderer({ width: 90, height: 30 })
  const client = fakeClient(script)
  const app = new App(setup.renderer, client as unknown as AgentClient, () => {})
  await app.start()
  await setup.waitForVisualIdle()
  return { app, client, setup }
}

test("renders header, status and a conversation with tool calls", async () => {
  const { app, setup } = await mount([
    [
      { type: "run_start", session_id: "session-123456789" },
      { type: "tool_start", id: "c1", tool: "read_file", args: { path: "src/auth/login.ts" } },
      { type: "tool_end", id: "c1", tool: "read_file", status: "success", result: "..." },
      { type: "agent_message", content: "I found the issue." },
      { type: "final", content: "I found the issue." },
    ],
  ])
  let frame = setup.captureCharFrame()
  expect(frame).toContain("⚡ Coding Agent")
  expect(frame).toContain("/work/project")
  expect(frame).toContain("Ready")

  await app.submit("Fix the authentication bug")
  // Agent replies are markdown, parsed asynchronously.
  frame = await waitForText(setup, "I found the issue.")
  expect(frame).toContain("> Fix the authentication bug")
  expect(frame).toContain('read_file("src/auth/login.ts")')
  expect(frame).toContain("I found the issue.")
  expect(frame).toContain("session session-")
})

test("shows a diff approval and sends the decision", async () => {
  const { app, client, setup } = await mount([
    [
      { type: "run_start", session_id: "s" },
      {
        type: "approval_required",
        tool_call_id: "c1",
        action: "edit_file",
        arguments: { path: "login.ts" },
        risk: "sensitive",
        reason: "modifies login.ts",
        diff: "--- a/login.ts\n+++ b/login.ts\n@@ -1 +1 @@\n-old condition\n+new condition\n",
      },
    ],
    [
      { type: "tool_start", id: "c1", tool: "edit_file", args: { path: "login.ts" } },
      { type: "tool_end", id: "c1", tool: "edit_file", status: "success", result: "Edited login.ts" },
      { type: "final", content: "" },
    ],
  ])
  await app.submit("fix login")
  await setup.waitForVisualIdle()
  let frame = setup.captureCharFrame()
  expect(frame).toContain("Approval Required")
  expect(frame).toContain("-old condition")
  expect(frame).toContain("+new condition")
  expect(frame).toContain("[Y] Allow")
  expect(frame).toContain("Waiting for approval")

  await app.submit("y")
  await setup.waitForVisualIdle()
  frame = setup.captureCharFrame()
  expect(client.calls.at(-1)).toEqual({ kind: "approval", args: ["s", true, { always: false }, expect.anything()] })
  expect(frame).toContain("✓ Allowed")
  expect(frame).toContain('edit_file("login.ts")')
})

test("free text while an approval is pending rejects it with feedback", async () => {
  const { app, client } = await mount([
    [{ type: "approval_required", tool_call_id: "c1", action: "run_command", arguments: { command: "make" }, risk: "sensitive", reason: "r" }],
    [{ type: "final", content: "ok" }],
  ])
  await app.submit("do it")
  await app.submit("use npm test instead")
  expect(client.calls.at(-1)).toMatchObject({ kind: "message", args: [expect.any(String), "use npm test instead", expect.anything()] })
  expect(app.store.state.items.some((i) => i.kind === "approval" && i.decision === "rejected")).toBe(true)
})

test("slash commands", async () => {
  const { app, setup } = await mount([])
  await app.submit("/help")
  await app.submit("/bogus")
  await setup.waitForVisualIdle()
  const frame = setup.captureCharFrame()
  expect(frame).toContain("Commands:")
  expect(frame).toContain("Unknown command /bogus")
})

test("reports an unreachable agent", async () => {
  setup = await createTestRenderer({ width: 90, height: 20 })
  const client = { health: async () => Promise.reject(new Error("Cannot reach the agent at http://x")) }
  const app = new App(setup.renderer, client as unknown as AgentClient, () => {})
  await app.start()
  await setup.waitForVisualIdle()
  const frame = setup.captureCharFrame()
  expect(frame).toContain("Cannot reach the agent")
  expect(frame).toContain("uv run coding-agent serve")
  expect(app.store.state.connection).toBe("disconnected")
})
