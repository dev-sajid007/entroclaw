import { describe, expect, test } from "bun:test"

import { parseSSE, type AgentEvent, type ApprovalRequest } from "../src/api/events.ts"
import { formatCall } from "../src/components/tool.ts"
import { addUserMessage, fromHistory, initialState, reduce, resolveApproval, type AppState } from "../src/state/app-state.ts"

function streamOf(...parts: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const part of parts) controller.enqueue(encoder.encode(part))
      controller.close()
    },
  })
}

const run = (events: AgentEvent[], state: AppState = initialState()) => events.reduce(reduce, state)

const approval: ApprovalRequest = {
  type: "approval_required",
  tool_call_id: "c2",
  action: "write_file",
  arguments: { path: "a.py", content: "x" },
  risk: "sensitive",
  reason: "creates a.py",
  diff: "+x",
}

describe("parseSSE", () => {
  test("parses events split across chunks", async () => {
    const events = []
    for await (const e of parseSSE(streamOf('data: {"type":"agent_tok', 'en","content":"Hi"}\n\n', 'data: {"type":"final","content":"Hi"}\n\n'))) {
      events.push(e)
    }
    expect(events).toEqual([
      { type: "agent_token", content: "Hi" },
      { type: "final", content: "Hi" },
    ])
  })

  test("handles CRLF and a trailing event without blank line", async () => {
    const events = []
    for await (const e of parseSSE(streamOf('data: {"type":"final","content":"a"}\r\n\r\ndata: {"type":"error","message":"b"}'))) events.push(e)
    expect(events.map((e) => e.type)).toEqual(["final", "error"])
  })
})

describe("reducer", () => {
  test("streams tokens into one agent message and finalizes it", () => {
    const state = run([
      { type: "run_start", session_id: "s1" },
      { type: "agent_token", content: "Hel" },
      { type: "agent_token", content: "lo" },
      { type: "agent_message", content: "Hello" },
      { type: "final", content: "Hello" },
    ])
    expect(state.items).toEqual([{ kind: "agent", text: "Hello", streaming: false }])
    expect(state.busy).toBe(false)
    expect(state.sessionId).toBe("s1")
  })

  test("tracks tool lifecycle and streamed output", () => {
    const state = run([
      { type: "tool_start", id: "c1", tool: "run_command", args: { command: "pytest" } },
      { type: "tool_output", tool: "run_command", content: "line 1\n" },
      { type: "tool_output", tool: "run_command", content: "line 2\n" },
      { type: "tool_end", id: "c1", tool: "run_command", status: "success", result: "ok", duration: 1.2 },
    ])
    expect(state.items).toHaveLength(1)
    expect(state.items[0]).toMatchObject({ kind: "tool", status: "success", output: "line 1\nline 2\n", duration: 1.2 })
  })

  test("rejected tools without a start still appear", () => {
    const state = run([{ type: "tool_end", id: "c9", tool: "write_file", status: "rejected", result: "The user rejected" }])
    expect(state.items[0]).toMatchObject({ kind: "tool", tool: "write_file", status: "rejected" })
  })

  test("approval pauses and resolution marks the card", () => {
    let state = run([{ type: "run_start", session_id: "s" }, approval])
    expect(state.pendingApproval?.action).toBe("write_file")
    expect(state.busy).toBe(false)
    state = resolveApproval(state, "approved")
    expect(state.pendingApproval).toBeUndefined()
    expect(state.items.at(-1)).toMatchObject({ kind: "approval", decision: "approved" })
  })

  test("usage accumulates and errors stop the run", () => {
    let state = addUserMessage(initialState(), "hi")
    state = run(
      [
        { type: "usage", input_tokens: 10, output_tokens: 5, total_tokens: 15 },
        { type: "usage", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
        { type: "error", message: "boom" },
      ],
      state,
    )
    expect(state.tokens).toEqual({ input: 11, output: 6 })
    expect(state.busy).toBe(false)
    expect(state.items.at(-1)).toEqual({ kind: "error", text: "boom" })
  })

  test("unchanged items keep their identity", () => {
    const before = run([{ type: "agent_message", content: "first" }])
    const after = reduce(before, { type: "tool_start", id: "c", tool: "list_files", args: {} })
    expect(after.items[0]).toBe(before.items[0]!)
  })

  test("rebuilds history when resuming", () => {
    const state = fromHistory(
      initialState(),
      [
        { role: "user", content: "fix it" },
        { role: "agent", content: "", tool_calls: [{ id: "c1", tool: "read_file", args: { path: "a.py" } }] },
        { role: "tool", id: "c1", tool: "read_file", status: "success", content: "1  x" },
      ],
      approval,
    )
    expect(state.items.map((i) => i.kind)).toEqual(["user", "tool", "approval"])
    expect(state.items[1]).toMatchObject({ status: "success" })
    expect(state.pendingApproval).toBe(approval)
  })
})

test("formatCall shows the primary argument", () => {
  expect(formatCall("read_file", { path: "src/app.ts" })).toBe('read_file("src/app.ts")')
  expect(formatCall("run_command", { command: "echo a\necho b" })).toBe('run_command("echo a…")')
  expect(formatCall("git_status", {})).toBe("git_status()")
  expect(formatCall("update_todos", { todos: [{}, {}, {}] })).toBe("update_todos(3 items)")
})

test("shortenPath abbreviates home and long paths", async () => {
  const { shortenPath } = await import("../src/components/header.ts")
  expect(shortenPath("/home/me/project", 50, "/home/me")).toBe("~/project")
  expect(shortenPath("/very/long/path/to/some/deeply/nested/workspace", 20, "/home/me")).toBe("…ly/nested/workspace")
})
