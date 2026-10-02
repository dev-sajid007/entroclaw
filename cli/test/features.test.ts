import { afterEach, describe, expect, test } from "bun:test"
import { mkdtempSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createTestRenderer, type TestRendererSetup } from "@opentui/core/testing"

import type { AgentClient } from "../src/api/agent.ts"
import type { AgentEvent, ApprovalRequest } from "../src/api/events.ts"
import { App, relativeTime } from "../src/app.ts"
import { fromHistory, initialState, reduce, resolveApproval } from "../src/state/app-state.ts"
import { PromptHistory } from "../src/state/history.ts"
import { waitForText } from "./support.ts"

type Script = AgentEvent[][]

function fakeClient(script: Script = [], extra: Record<string, unknown> = {}) {
  const calls: { kind: string; args: unknown[] }[] = []
  let next = 0
  async function* play(): AsyncGenerator<AgentEvent> {
    for (const event of script[next++] ?? []) yield event
  }
  return {
    calls,
    health: async () => ({ status: "ok", workspace: "/work/project", model: "m", require_approval: true, tools: [], mcp_errors: {} }),
    createSession: async () => "new-session",
    getSession: async (id: string) => ({ session_id: id, messages: [], pending_approval: null, allow_rules: [] }),
    listSessions: async () => [],
    sendMessage: (...args: unknown[]) => (calls.push({ kind: "message", args }), play()),
    respondApproval: (...args: unknown[]) => (calls.push({ kind: "approval", args }), play()),
    ...extra,
  }
}

let setup: TestRendererSetup | undefined

afterEach(() => {
  setup?.renderer.destroy()
  setup = undefined
})

async function mount(client: ReturnType<typeof fakeClient>, history?: PromptHistory, sessionId?: string) {
  setup = await createTestRenderer({ width: 100, height: 36 })
  const app = new App(setup.renderer, client as unknown as AgentClient, () => {}, history)
  await app.start(sessionId)
  await setup.waitForVisualIdle()
  return { app, setup }
}

const editApproval: ApprovalRequest = {
  type: "approval_required",
  tool_call_id: "c1",
  action: "edit_file",
  arguments: { path: "a.py" },
  risk: "sensitive",
  reason: "modifies a.py",
  diff: "@@ -1 +1 @@\n-a\n+b\n",
  allow_always: true,
  rule: "edit_file",
}

describe("state", () => {
  test("context_compacted adds a notice", () => {
    const state = reduce(initialState(), { type: "context_compacted", removed: 12, fallback: false })
    expect(state.items.at(-1)).toEqual({ kind: "info", text: "Context: 12 older messages summarized to stay within the model's limit." })
  })

  test("always decision and summarized history", () => {
    let state = reduce(initialState(), editApproval)
    state = resolveApproval(state, "always")
    expect(state.items.at(-1)).toMatchObject({ kind: "approval", decision: "always" })
    const resumed = fromHistory(initialState(), [{ role: "user", content: "hi" }], null, "they asked for X")
    expect(resumed.items[0]).toEqual({ kind: "info", text: "Earlier conversation (summarized):\nthey asked for X" })
  })

  test("relativeTime", () => {
    expect(relativeTime(1000, 1030)).toBe("just now")
    expect(relativeTime(1000, 1000 + 125)).toBe("2m ago")
    expect(relativeTime(0, 3 * 86400 + 5)).toBe("3d ago")
  })
})

describe("prompt history", () => {
  test("browses with a draft and persists", async () => {
    const path = join(mkdtempSync(join(tmpdir(), "hist-")), "h.json")
    const history = new PromptHistory(path)
    await history.load()
    history.add("first")
    history.add("second")
    history.add("second") // consecutive duplicates collapse
    expect(history.previous("draft")).toBe("second")
    expect(history.previous("ignored")).toBe("first")
    expect(history.previous("ignored")).toBeNull()
    expect(history.next()).toBe("second")
    expect(history.next()).toBe("draft")
    expect(history.next()).toBeNull()
    await Bun.sleep(20)
    expect(JSON.parse(readFileSync(path, "utf8"))).toEqual(["first", "second"])
    const reloaded = new PromptHistory(path)
    await reloaded.load()
    expect(reloaded.all).toEqual(["first", "second"])
  })
})

describe("app", () => {
  test("always-allow is offered and sent", async () => {
    const client = fakeClient([[{ type: "run_start", session_id: "s" }, editApproval], [{ type: "final", content: "" }]])
    const { app } = await mount(client)
    await app.submit("edit it")
    await setup!.waitForVisualIdle()
    expect(setup!.captureCharFrame()).toContain("[A] Always allow edit_file")
    await app.submit("a")
    expect(client.calls.at(-1)).toEqual({ kind: "approval", args: ["s", true, { always: true }, expect.anything()] })
    await setup!.waitForVisualIdle()
    expect(setup!.captureCharFrame()).toContain("always for edit_file this session")
  })

  test("'a' is not accepted for high-risk actions", async () => {
    const high = { ...editApproval, risk: "high" as const, allow_always: false }
    const client = fakeClient([[high], [{ type: "final", content: "" }]])
    const { app } = await mount(client)
    await app.submit("go")
    await setup!.waitForVisualIdle()
    expect(setup!.captureCharFrame()).not.toContain("[A] Always allow")
    await app.submit("a")
    // Treated as feedback text, which rejects the action.
    expect(client.calls.at(-1)).toMatchObject({ kind: "message", args: ["new-session", "a", expect.anything()] })
  })

  test("/sessions lists and /resume loads by number", async () => {
    const now = Date.now() / 1000
    const client = fakeClient([], {
      listSessions: async () => [
        { session_id: "aaaa1111", title: "Fix login", created_at: now - 7200, updated_at: now - 60 },
        { session_id: "bbbb2222", title: "Add feature", created_at: now - 9000, updated_at: now - 7200 },
      ],
      getSession: async (id: string) => ({
        session_id: id,
        messages: [{ role: "user", content: `history of ${id}` }],
        pending_approval: null,
        summary: "",
      }),
    })
    const { app } = await mount(client)
    await app.submit("/sessions")
    await setup!.waitForVisualIdle()
    let frame = setup!.captureCharFrame()
    expect(frame).toContain("1. 1m ago    Fix login  (aaaa1111)")
    expect(frame).toContain("2. 2h ago    Add feature  (bbbb2222)")
    await app.submit("/resume 2")
    await setup!.waitForVisualIdle()
    frame = setup!.captureCharFrame()
    expect(app.store.state.sessionId).toBe("bbbb2222")
    expect(frame).toContain("> history of bbbb2222")
    await app.submit("/resume aaaa")
    expect(app.store.state.sessionId).toBe("aaaa1111")
  })

  test("/undo, /compact, /memory, /rules report results", async () => {
    const client = fakeClient([], {
      undo: async () => ({ restored: ["a.py"], deleted: ["new.py"], conflicts: ["b.py"] }),
      compact: async () => ({ removed: 8 }),
      getMemory: async () => ({ instructions_source: "AGENTS.md", project: "- use pnpm", global: "" }),
      getSession: async (id: string) => ({ session_id: id, messages: [], pending_approval: null, allow_rules: ["edit_file", "run_command:npm test"] }),
    })
    const { app } = await mount(client)
    await app.submit("/undo")
    await app.submit("/compact")
    await app.submit("/memory")
    await app.submit("/rules")
    await setup!.waitForVisualIdle()
    const frame = setup!.captureCharFrame()
    expect(frame).toContain("Undo: restored a.py; deleted new.py (created by the agent); skipped b.py")
    expect(frame).toContain("Compacted: 8 older messages summarized.")
    expect(frame).toContain("Project instructions: AGENTS.md")
    expect(frame).toContain("- use pnpm")
    expect(frame).toContain("run_command:npm test")
  })

  test("agent replies render as markdown with code blocks", async () => {
    const reply = "## Plan\n\nRun **tests** with `bun test`:\n\n```ts\nconst answer = 42\n```"
    const client = fakeClient([[{ type: "agent_message", content: reply }, { type: "final", content: reply }]])
    const { app } = await mount(client)
    await app.submit("explain")
    await waitForText(setup!, "const answer = 42")
    const frame = await waitForText(setup!, "Run tests with bun test:")
    expect(frame).toContain("Plan")
    expect(frame).not.toContain("## Plan")
    expect(frame).not.toContain("**tests**")
    expect(frame).toContain("Run tests with bun test:")
  })

  test("streaming tokens update the same markdown view", async () => {
    const client = fakeClient([
      [
        { type: "agent_token", content: "Hello " },
        { type: "agent_token", content: "world" },
        { type: "agent_message", content: "Hello world" },
        { type: "final", content: "Hello world" },
      ],
    ])
    const { app } = await mount(client)
    await app.submit("hi")
    await waitForText(setup!, "Hello world")
  })

  test("multi-line input with Ctrl+J and history with Up", async () => {
    const client = fakeClient([[{ type: "final", content: "" }], [{ type: "final", content: "" }]])
    const history = new PromptHistory(null)
    const { app } = await mount(client, history)
    const keys = setup!.mockInput
    await keys.typeText("line one")
    keys.pressKey("LINEFEED")
    await keys.typeText("line two")
    await setup!.waitForVisualIdle()
    expect(setup!.captureCharFrame()).toContain("line two")
    keys.pressEnter()
    await Bun.sleep(20)
    expect(client.calls[0]).toMatchObject({ kind: "message", args: ["new-session", "line one\nline two", expect.anything()] })
    expect(history.all).toEqual(["line one\nline two"])

    keys.pressKey("ARROW_UP")
    await setup!.waitForVisualIdle()
    expect(setup!.captureCharFrame()).toContain("line one")
    keys.pressEnter()
    await Bun.sleep(20)
    expect(client.calls[1]).toMatchObject({ kind: "message", args: ["new-session", "line one\nline two", expect.anything()] })
    expect(app.store.state.items.filter((i) => i.kind === "user")).toHaveLength(2)
  })
})
