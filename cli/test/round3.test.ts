import { afterEach, expect, test } from "bun:test"
import { createTestRenderer, type TestRendererSetup } from "@opentui/core/testing"

import type { AgentClient } from "../src/api/agent.ts"
import type { AgentEvent } from "../src/api/events.ts"
import { App } from "../src/app.ts"
import { todoChunks } from "../src/components/todos.ts"
import { initialState, reduce } from "../src/state/app-state.ts"

const TODOS = [
  { content: "Read the login code", status: "completed" as const },
  { content: "Fix the condition", status: "in_progress" as const },
  { content: "Run the tests", status: "pending" as const },
]

let setup: TestRendererSetup | undefined
afterEach(() => {
  setup?.renderer.destroy()
  setup = undefined
})

function fakeClient(script: AgentEvent[][] = [], extra: Record<string, unknown> = {}) {
  const calls: { kind: string; args: unknown[] }[] = []
  let next = 0
  async function* play(): AsyncGenerator<AgentEvent> {
    for (const event of script[next++] ?? []) yield event
  }
  return {
    calls,
    health: async () => ({ status: "ok", workspace: "/w", model: "openai:gpt-4.1-mini", sandbox: "bwrap (no network)", require_approval: true, tools: [], mcp_errors: {} }),
    createSession: async () => "s1",
    listModels: async () => ({ default: "openai:gpt-4.1-mini", models: ["openai:gpt-4.1-mini", "anthropic:claude-opus-5-5"], providers: [] }),
    setModel: async (...args: unknown[]) => (calls.push({ kind: "setModel", args }), { model: args[1] as string }),
    setMode: async (...args: unknown[]) => (calls.push({ kind: "setMode", args }), { mode: args[1] }),
    sendMessage: (...args: unknown[]) => (calls.push({ kind: "message", args }), play()),
    respondApproval: (...args: unknown[]) => (calls.push({ kind: "approval", args }), play()),
    ...extra,
  }
}

async function mount(client: ReturnType<typeof fakeClient>) {
  setup = await createTestRenderer({ width: 100, height: 30 })
  const app = new App(setup.renderer, client as unknown as AgentClient, () => {})
  await app.start()
  await setup.waitForVisualIdle()
  return app
}

test("reducer stores todos", () => {
  expect(reduce(initialState(), { type: "todos", todos: TODOS }).todos).toEqual(TODOS)
})

test("todo chunks show progress and icons", () => {
  const text = todoChunks(TODOS).map((c) => c.text).join("")
  expect(text).toBe("Tasks 1/3\n☑ Read the login code\n◐ Fix the condition\n☐ Run the tests")
})

test("todo panel appears when the agent sets a list", async () => {
  const app = await mount(fakeClient([[{ type: "todos", todos: TODOS }, { type: "final", content: "" }]]))
  expect(setup!.captureCharFrame()).not.toContain("Tasks")
  await app.submit("plan it")
  await setup!.waitForVisualIdle()
  const frame = setup!.captureCharFrame()
  expect(frame).toContain("Tasks 1/3")
  expect(frame).toContain("◐ Fix the condition")
  expect(frame).toContain("sandboxed")
})

test("/plan and /go switch modes and show the badge", async () => {
  const client = fakeClient([[{ type: "final", content: "" }], [{ type: "final", content: "" }]])
  const app = await mount(client)
  await app.submit("/plan fix the login bug")
  await setup!.waitForVisualIdle()
  expect(client.calls[0]).toEqual({ kind: "setMode", args: ["s1", "plan"] })
  expect(client.calls[1]).toMatchObject({ kind: "message", args: ["s1", "fix the login bug", expect.anything()] })
  expect(setup!.captureCharFrame()).toContain(" PLAN ")

  await app.submit("/go")
  await setup!.waitForVisualIdle()
  expect(client.calls[2]).toEqual({ kind: "setMode", args: ["s1", "build"] })
  expect(client.calls[3]).toMatchObject({ kind: "message", args: ["s1", "Proceed with the plan.", expect.anything()] })
  expect(setup!.captureCharFrame()).not.toContain(" PLAN ")
})

test("/model lists and switches", async () => {
  const client = fakeClient()
  const app = await mount(client)
  await app.submit("/model")
  await setup!.waitForVisualIdle()
  let frame = setup!.captureCharFrame()
  expect(frame).toContain("* 1. openai:gpt-4.1-mini")
  expect(frame).toContain("  2. anthropic:claude-opus-5-5")
  await app.submit("/model 2")
  await setup!.waitForVisualIdle()
  expect(client.calls.at(-1)).toEqual({ kind: "setModel", args: ["s1", "anthropic:claude-opus-5-5"] })
  frame = setup!.captureCharFrame()
  expect(frame).toContain("Model: anthropic:claude-opus-5-5")
  expect(app.store.state.model).toBe("anthropic:claude-opus-5-5")
})
