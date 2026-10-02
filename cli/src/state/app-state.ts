// UI state, kept separate from LangGraph state. The server stays the source of truth for agent execution;
// this only mirrors what the user should see. Updates are immutable so views can diff by reference.

import type { HistoryMessage } from "../api/agent.ts"
import type { AgentEvent, ApprovalRequest, ToolStatus } from "../api/events.ts"

export type ConnectionStatus = "connecting" | "connected" | "disconnected"

export type ApprovalDecision = "approved" | "always" | "rejected"

export type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "agent"; text: string; streaming: boolean }
  | {
      kind: "tool"
      id: string
      tool: string
      args: Record<string, unknown>
      status: ToolStatus
      output: string
      result?: string
      duration?: number
    }
  | { kind: "approval"; request: ApprovalRequest; decision?: ApprovalDecision }
  | { kind: "error"; text: string }
  | { kind: "info"; text: string }

export interface AppState {
  connection: ConnectionStatus
  sessionId?: string
  workspace?: string
  model?: string
  items: ChatItem[]
  busy: boolean
  pendingApproval?: ApprovalRequest
  status: string
  tokens: { input: number; output: number }
}

export const MAX_TOOL_OUTPUT_CHARS = 8000

export function initialState(): AppState {
  return { connection: "connecting", items: [], busy: false, status: "Connecting…", tokens: { input: 0, output: 0 } }
}

function replaceLast<T extends ChatItem>(items: ChatItem[], match: (item: ChatItem) => item is T, update: (item: T) => ChatItem): ChatItem[] | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i]!
    if (match(item)) {
      const next = items.slice()
      next[i] = update(item)
      return next
    }
  }
  return null
}

const isTool = (item: ChatItem): item is Extract<ChatItem, { kind: "tool" }> => item.kind === "tool"

export function reduce(state: AppState, event: AgentEvent): AppState {
  switch (event.type) {
    case "run_start":
      return { ...state, sessionId: event.session_id, busy: true, status: "Thinking…" }

    case "agent_token": {
      const last = state.items.at(-1)
      if (last?.kind === "agent" && last.streaming) {
        return { ...state, items: [...state.items.slice(0, -1), { ...last, text: last.text + event.content }] }
      }
      return { ...state, status: "Responding…", items: [...state.items, { kind: "agent", text: event.content, streaming: true }] }
    }

    case "agent_message": {
      const last = state.items.at(-1)
      const done: ChatItem = { kind: "agent", text: event.content, streaming: false }
      if (last?.kind === "agent" && last.streaming) return { ...state, items: [...state.items.slice(0, -1), done] }
      return { ...state, items: [...state.items, done] }
    }

    case "tool_start":
      return {
        ...state,
        status: `Running ${event.tool}…`,
        items: [
          ...finishStreaming(state.items),
          { kind: "tool", id: event.id, tool: event.tool, args: event.args, status: "running", output: "" },
        ],
      }

    case "tool_output": {
      const items = replaceLast(
        state.items,
        (item): item is Extract<ChatItem, { kind: "tool" }> => isTool(item) && item.status === "running" && item.tool === event.tool,
        (item) => ({ ...item, output: (item.output + event.content).slice(-MAX_TOOL_OUTPUT_CHARS) }),
      )
      return items ? { ...state, items } : state
    }

    case "tool_end": {
      const end = { status: event.status, result: event.result, duration: event.duration }
      const items = replaceLast(state.items, (item): item is Extract<ChatItem, { kind: "tool" }> => isTool(item) && item.id === event.id, (item) => ({
        ...item,
        ...end,
      }))
      if (items) return { ...state, status: "Thinking…", items }
      // Rejected and denied calls never started, so there is no running item to update.
      return {
        ...state,
        status: "Thinking…",
        items: [...state.items, { kind: "tool", id: event.id, tool: event.tool, args: {}, output: "", ...end }],
      }
    }

    case "approval_required":
      return {
        ...state,
        busy: false,
        pendingApproval: event,
        status: "Waiting for approval",
        items: [...finishStreaming(state.items), { kind: "approval", request: event }],
      }

    case "context_compacted": {
      const how = event.fallback ? "dropped (summarization failed)" : "summarized"
      return { ...state, items: [...state.items, { kind: "info", text: `Context: ${event.removed} older messages ${how} to stay within the model's limit.` }] }
    }

    case "usage":
      return { ...state, tokens: { input: state.tokens.input + event.input_tokens, output: state.tokens.output + event.output_tokens } }

    case "final":
      return { ...state, busy: false, status: "Ready", items: finishStreaming(state.items) }

    case "error":
      return { ...state, busy: false, status: "Error", items: [...finishStreaming(state.items), { kind: "error", text: event.message }] }
  }
}

function finishStreaming(items: ChatItem[]): ChatItem[] {
  const last = items.at(-1)
  if (last?.kind === "agent" && last.streaming) return [...items.slice(0, -1), { ...last, streaming: false }]
  return items
}

export function addUserMessage(state: AppState, text: string): AppState {
  return { ...state, busy: true, status: "Thinking…", items: [...state.items, { kind: "user", text }] }
}

/** Record the user's answer on the pending approval card. */
export function resolveApproval(state: AppState, decision: ApprovalDecision): AppState {
  const approved = decision !== "rejected"
  const items = replaceLast(
    state.items,
    (item): item is Extract<ChatItem, { kind: "approval" }> => item.kind === "approval" && !item.decision,
    (item) => ({ ...item, decision }),
  )
  return { ...state, pendingApproval: undefined, busy: true, status: approved ? "Approved — running…" : "Rejected", items: items ?? state.items }
}

export function addNotice(state: AppState, text: string, kind: "info" | "error" = "info"): AppState {
  return { ...state, items: [...state.items, { kind, text }] }
}

/** Rebuild the conversation from the server's stored history (used when resuming a session). */
export function fromHistory(state: AppState, messages: HistoryMessage[], pending: ApprovalRequest | null, summary = ""): AppState {
  const items: ChatItem[] = []
  if (summary) items.push({ kind: "info", text: `Earlier conversation (summarized):\n${summary}` })
  const toolIndex = new Map<string, number>()
  for (const message of messages) {
    if (message.role === "user") items.push({ kind: "user", text: message.content })
    else if (message.role === "agent") {
      if (message.content) items.push({ kind: "agent", text: message.content, streaming: false })
      for (const call of message.tool_calls ?? []) {
        toolIndex.set(call.id, items.length)
        items.push({ kind: "tool", id: call.id, tool: call.tool, args: call.args, status: "running", output: "" })
      }
    } else if (message.role === "tool" && message.id) {
      const index = toolIndex.get(message.id)
      const item = index === undefined ? undefined : items[index]
      if (item?.kind === "tool") {
        const rejected = message.content.startsWith("The user rejected")
        const denied = message.content.startsWith("Denied by policy")
        const status: ToolStatus = rejected ? "rejected" : denied ? "denied" : message.status === "error" ? "error" : "success"
        items[index!] = { ...item, status, result: message.content }
      }
    }
  }
  if (pending) items.push({ kind: "approval", request: pending })
  return {
    ...state,
    items,
    pendingApproval: pending ?? undefined,
    busy: false,
    status: pending ? "Waiting for approval" : "Ready",
  }
}

export type Listener = (state: AppState) => void

export class AppStore {
  private listeners = new Set<Listener>()

  constructor(public state: AppState = initialState()) {}

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  update(fn: (state: AppState) => AppState): void {
    const next = fn(this.state)
    if (next === this.state) return
    this.state = next
    for (const listener of this.listeners) listener(next)
  }

  dispatch(event: AgentEvent): void {
    this.update((state) => reduce(state, event))
  }
}
