import { BoxRenderable, type CliRenderer, type KeyEvent } from "@opentui/core"

import { AgentApiError, type AgentClient, type MemoryScope, type SessionSummary } from "./api/agent.ts"
import type { AgentEvent } from "./api/events.ts"
import { ChatView } from "./components/chat.ts"
import { Header } from "./components/header.ts"
import { Input } from "./components/input.ts"
import { StatusBar } from "./components/status.ts"
import { TodoPanel } from "./components/todos.ts"
import { addNotice, addUserMessage, AppStore, fromHistory, resolveApproval, type AppState } from "./state/app-state.ts"
import type { PromptHistory } from "./state/history.ts"

const HELP = [
  "Commands:",
  "  /new                 start a new session",
  "  /sessions            list recent sessions in this workspace",
  "  /resume <n|id>       resume a session (number from /sessions, or an id prefix)",
  "  /undo                revert the files the agent changed in its last turn",
  "  /compact             summarize older messages to free up context",
  "  /memory              show project instructions and remembered notes",
  "  /forget [project|global]  clear remembered notes (default: project)",
  "  /rules               show actions you chose to always allow this session",
  "  /model [n|name]      list models or switch this session's model",
  "  /plan [request]      plan mode: read-only investigation, the agent proposes a plan",
  "  /go [message]        leave plan mode and carry out the plan",
  "  /session             show the current session id",
  "  /clear               clear the screen (the session keeps its history)",
  "  /quit                exit",
  "Keys: Enter send · Ctrl+J newline · ↑/↓ prompt history · Esc cancel · PgUp/PgDn scroll · Ctrl+C exit",
  "Approvals: y allow · a always allow (this session) · n reject · any other text rejects with feedback.",
].join("\n")

const APPROVE = new Set(["y", "yes", "allow"])
const ALWAYS = new Set(["a", "always"])
const REJECT = new Set(["n", "no", "reject"])

export function relativeTime(epochSeconds: number, now = Date.now() / 1000): string {
  const seconds = Math.max(0, now - epochSeconds)
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export class App {
  readonly store = new AppStore()
  private readonly header: Header
  private readonly chat: ChatView
  private readonly input: Input
  private readonly statusBar: StatusBar
  private readonly todoPanel: TodoPanel
  private abort?: AbortController
  private spinner?: ReturnType<typeof setInterval>
  /** Result of the last /sessions, so /resume can take a number. */
  private listedSessions: SessionSummary[] = []

  constructor(
    private readonly renderer: CliRenderer,
    private readonly client: AgentClient,
    private readonly onQuit: () => void = () => renderer.destroy(),
    history?: PromptHistory,
  ) {
    const layout = new BoxRenderable(renderer, { flexDirection: "column", width: "100%", height: "100%" })
    this.header = new Header(renderer)
    this.chat = new ChatView(renderer)
    this.input = new Input(renderer, (value) => void this.submit(value), history)
    this.statusBar = new StatusBar(renderer)
    this.todoPanel = new TodoPanel(renderer)
    layout.add(this.header.view)
    layout.add(this.chat.view)
    layout.add(this.todoPanel.view)
    layout.add(this.input.view)
    layout.add(this.statusBar.view)
    renderer.root.add(layout)

    this.store.subscribe((state) => this.render(state))
    renderer.keyInput.on("keypress", (key: KeyEvent) => this.onKey(key))
    this.render(this.store.state)
  }

  /** Connect, then resume `sessionId`, or the latest session when `continueLatest`, or start a new one. */
  async start(sessionId?: string, options: { continueLatest?: boolean } = {}): Promise<void> {
    try {
      const health = await this.client.health()
      this.store.update((s) => ({
        ...s,
        connection: "connected",
        workspace: health.workspace,
        model: health.model,
        sandbox: health.sandbox,
        status: "Ready",
      }))
      for (const [server, error] of Object.entries(health.mcp_errors)) {
        this.store.update((s) => addNotice(s, `MCP server "${server}" failed to load: ${error}`, "error"))
      }
      if (!sessionId && options.continueLatest) {
        sessionId = (await this.client.listSessions(1))[0]?.session_id
        if (!sessionId) this.store.update((s) => addNotice(s, "No previous session in this workspace; starting a new one."))
      }
      if (sessionId) {
        await this.resume(sessionId)
      } else {
        const id = await this.client.createSession()
        this.store.update((s) => ({ ...s, sessionId: id }))
      }
    } catch (error) {
      this.store.update((s) => ({
        ...addNotice(s, `${(error as Error).message}\nStart the agent with:  cd agent && uv run coding-agent serve`, "error"),
        connection: "disconnected",
        status: "Disconnected",
      }))
    }
  }

  async submit(value: string): Promise<void> {
    if (value.startsWith("/")) return this.command(value)
    const state = this.store.state
    if (state.connection !== "connected" || !state.sessionId) {
      this.store.update((s) => addNotice(s, "Not connected to the agent.", "error"))
      return
    }
    if (state.busy) {
      this.store.update((s) => addNotice(s, "The agent is still working — press Esc to cancel it first."))
      return
    }
    const sessionId = state.sessionId
    if (state.pendingApproval) {
      const answer = value.toLowerCase()
      const always = ALWAYS.has(answer) && state.pendingApproval.allow_always === true
      if (APPROVE.has(answer) || REJECT.has(answer) || always) {
        const approved = !REJECT.has(answer)
        this.store.update((s) => resolveApproval(s, always ? "always" : approved ? "approved" : "rejected"))
        return this.run((signal) => this.client.respondApproval(sessionId, approved, { always }, signal))
      }
      // Any other text rejects the pending action and passes the text along as feedback (server-side rule).
      this.store.update((s) => addUserMessage(resolveApproval(s, "rejected"), value))
      return this.run((signal) => this.client.sendMessage(sessionId, value, signal))
    }
    this.store.update((s) => addUserMessage(s, value))
    return this.run((signal) => this.client.sendMessage(sessionId, value, signal))
  }

  cancel(): void {
    this.abort?.abort()
  }

  private async run(open: (signal: AbortSignal) => AsyncGenerator<AgentEvent>): Promise<void> {
    const abort = new AbortController()
    this.abort = abort
    try {
      for await (const event of open(abort.signal)) this.store.dispatch(event)
      if (this.store.state.busy) {
        // The stream ended without final/approval/error (e.g. the server restarted mid-run).
        this.store.update((s) => ({ ...addNotice(s, "The run ended unexpectedly.", "error"), busy: false, status: "Ready" }))
      }
    } catch (error) {
      if (abort.signal.aborted) {
        this.store.update((s) => ({ ...addNotice(s, "Cancelled."), busy: false, status: "Ready" }))
      } else {
        const message = error instanceof AgentApiError ? error.message : `Unexpected error: ${(error as Error).message}`
        this.store.update((s) => ({ ...addNotice(s, message, "error"), busy: false, status: "Error" }))
      }
    } finally {
      if (this.abort === abort) this.abort = undefined
    }
  }

  private notice(text: string, kind: "info" | "error" = "info"): void {
    this.store.update((s) => addNotice(s, text, kind))
  }

  private async resume(sessionId: string): Promise<void> {
    const session = await this.client.getSession(sessionId)
    this.store.update((s) => ({
      ...fromHistory(s, session.messages, session.pending_approval, session.summary),
      sessionId,
      model: session.model ?? s.model,
      mode: session.mode ?? "build",
      todos: session.todos ?? [],
      tokens: { input: 0, output: 0 },
    }))
    this.notice(session.messages.length ? `Resumed session ${sessionId}` : `Session ${sessionId} has no messages yet`)
  }

  /** Commands that operate on the session's server state need it idle (no run in progress). */
  private idleSession(): string | null {
    const { sessionId, busy, connection } = this.store.state
    if (connection !== "connected" || !sessionId) {
      this.notice("Not connected to the agent.", "error")
      return null
    }
    if (busy) {
      this.notice("The agent is still working — press Esc to cancel it first.")
      return null
    }
    return sessionId
  }

  private async command(value: string): Promise<void> {
    const [name, ...args] = value.slice(1).trim().split(/\s+/)
    try {
      await this.runCommand(name ?? "", args, value)
    } catch (error) {
      this.notice((error as Error).message, "error")
    }
  }

  private async runCommand(name: string, args: string[], value: string): Promise<void> {
    switch (name) {
      case "help":
        this.store.update((s) => addNotice(s, HELP))
        return
      case "quit":
      case "exit":
        this.onQuit()
        return
      case "clear":
        this.store.update((s) => ({ ...s, items: [] }))
        return
      case "session":
        this.store.update((s) => addNotice(s, `Session: ${s.sessionId ?? "(none)"}`))
        return
      case "new": {
        if (this.store.state.busy) this.cancel()
        const id = await this.client.createSession()
        const model = (await this.client.health()).model
        this.store.update((s) => ({
          ...s,
          sessionId: id,
          model,
          mode: "build",
          todos: [],
          items: [],
          pendingApproval: undefined,
          busy: false,
          status: "Ready",
          tokens: { input: 0, output: 0 },
        }))
        this.notice(`New session ${id}`)
        return
      }
      case "sessions": {
        this.listedSessions = await this.client.listSessions(20)
        if (!this.listedSessions.length) {
          this.notice("No sessions in this workspace yet.")
          return
        }
        const current = this.store.state.sessionId
        const lines = this.listedSessions.map((session, index) => {
          const marker = session.session_id === current ? "*" : " "
          return `${marker}${String(index + 1).padStart(2)}. ${relativeTime(session.updated_at).padEnd(9)} ${session.title}  (${session.session_id.slice(0, 8)})`
        })
        this.notice(["Sessions (resume with /resume <n>):", ...lines].join("\n"))
        return
      }
      case "resume": {
        const target = args[0]
        if (!target) {
          this.notice("Usage: /resume <n|id>  (see /sessions)", "error")
          return
        }
        if (this.store.state.busy) this.cancel()
        if (!this.listedSessions.length) this.listedSessions = await this.client.listSessions(50)
        const byNumber = /^\d+$/.test(target) ? this.listedSessions[Number(target) - 1] : undefined
        const byPrefix = this.listedSessions.filter((session) => session.session_id.startsWith(target))
        const id = byNumber?.session_id ?? (byPrefix.length === 1 ? byPrefix[0]!.session_id : target)
        await this.resume(id)
        return
      }
      case "undo": {
        const sessionId = this.idleSession()
        if (!sessionId) return
        const result = await this.client.undo(sessionId)
        const lines: string[] = []
        if (result.restored.length) lines.push(`restored ${result.restored.join(", ")}`)
        if (result.deleted.length) lines.push(`deleted ${result.deleted.join(", ")} (created by the agent)`)
        if (result.conflicts.length) lines.push(`skipped ${result.conflicts.join(", ")} (changed since the agent wrote it)`)
        this.notice(lines.length ? `Undo: ${lines.join("; ")}` : "Nothing to undo (changes made through shell commands are not tracked).")
        return
      }
      case "compact": {
        const sessionId = this.idleSession()
        if (!sessionId) return
        const { removed } = await this.client.compact(sessionId)
        this.notice(removed ? `Compacted: ${removed} older messages summarized.` : "Nothing to compact yet.")
        return
      }
      case "memory": {
        const memory = await this.client.getMemory()
        const lines = [
          `Project instructions: ${memory.instructions_source ?? "none (add AGENTS.md to the workspace root)"}`,
          `Project notes:\n${memory.project || "  (none)"}`,
          `Global notes:\n${memory.global || "  (none)"}`,
        ]
        this.notice(lines.join("\n"))
        return
      }
      case "forget": {
        const scope = (args[0] ?? "project") as MemoryScope
        if (scope !== "project" && scope !== "global") {
          this.notice("Usage: /forget [project|global]", "error")
          return
        }
        await this.client.clearMemory(scope)
        this.notice(`Cleared ${scope} memory.`)
        return
      }
      case "model": {
        const { models } = await this.client.listModels()
        const current = this.store.state.model
        const target = args[0]
        if (!target) {
          const lines = models.map((m, i) => `${m === current ? "*" : " "}${String(i + 1).padStart(2)}. ${m}`)
          this.notice(["Models (switch with /model <n|name>; add more with MODELS in agent/.env):", ...lines].join("\n"))
          return
        }
        const sessionId = this.idleSession()
        if (!sessionId) return
        const spec = /^\d+$/.test(target) ? models[Number(target) - 1] : target
        if (!spec) {
          this.notice(`No model #${target}. Run /model to list them.`, "error")
          return
        }
        const { model } = await this.client.setModel(sessionId, spec)
        this.store.update((s) => ({ ...s, model }))
        this.notice(`Model: ${model}`)
        return
      }
      case "plan":
      case "go": {
        const sessionId = this.idleSession()
        if (!sessionId) return
        const mode = name === "plan" ? "plan" : "build"
        await this.client.setMode(sessionId, mode)
        this.store.update((s) => ({ ...s, mode }))
        if (mode === "plan") {
          this.notice("Plan mode: the agent can read and run read-only commands, but not change anything. /go to approve and build.")
          const request = args.join(" ").trim()
          if (request) await this.submit(request)
        } else {
          this.notice("Build mode: the agent can make changes again (with your approval).")
          await this.submit(args.join(" ").trim() || "Proceed with the plan.")
        }
        return
      }
      case "rules": {
        const sessionId = this.store.state.sessionId
        if (!sessionId) return
        const rules = (await this.client.getSession(sessionId)).allow_rules ?? []
        this.notice(rules.length ? ["Always allowed this session:", ...rules.map((r) => `  ${r}`)].join("\n") : "No always-allow rules in this session.")
        return
      }
      default:
        this.store.update((s) => addNotice(s, `Unknown command ${value}. Type /help.`, "error"))
    }
  }

  private onKey(key: KeyEvent): void {
    if (key.name === "escape" && this.store.state.busy) this.cancel()
    else if (key.name === "pageup") this.chat.scroll(-10)
    else if (key.name === "pagedown") this.chat.scroll(10)
  }

  private render(state: AppState): void {
    this.header.update(state)
    this.todoPanel.update(state)
    this.chat.sync(state.items)
    this.input.update(state)
    this.statusBar.update(state)
    if (state.busy && !this.spinner) {
      this.spinner = setInterval(() => {
        this.statusBar.tick()
        this.statusBar.update(this.store.state)
      }, 100)
    } else if (!state.busy && this.spinner) {
      clearInterval(this.spinner)
      this.spinner = undefined
    }
  }

  dispose(): void {
    if (this.spinner) clearInterval(this.spinner)
    this.abort?.abort()
  }
}
