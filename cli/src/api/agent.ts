import { parseSSE, type AgentEvent, type ApprovalRequest, type Todo } from "./events.ts"

export interface Health {
  status: string
  workspace: string
  model: string
  models?: string[]
  providers?: string[]
  sandbox?: string
  require_approval: boolean
  tools: string[]
  mcp_errors: Record<string, string>
}

export interface HistoryMessage {
  role: "user" | "agent" | "tool"
  content: string
  tool?: string
  id?: string
  status?: string
  tool_calls?: { id: string; tool: string; args: Record<string, unknown> }[]
}

export interface SessionState {
  session_id: string
  messages: HistoryMessage[]
  summary?: string
  allow_rules?: string[]
  model?: string
  mode?: Mode
  todos?: Todo[]
  pending_approval: ApprovalRequest | null
}

export type Mode = "build" | "plan"

export interface SessionSummary {
  session_id: string
  title: string
  created_at: number
  updated_at: number
}

export interface UndoResult {
  restored: string[]
  deleted: string[]
  conflicts: string[]
}

export interface MemoryState {
  instructions_source: string | null
  project: string
  global: string
}

export type MemoryScope = "project" | "global"

export class AgentApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
  }
}

export class AgentClient {
  constructor(
    readonly baseUrl: string,
    private readonly token?: string,
  ) {}

  health(): Promise<Health> {
    return this.json("GET", "/health")
  }

  async createSession(): Promise<string> {
    const body = await this.json<{ session_id: string }>("POST", "/sessions")
    return body.session_id
  }

  getSession(sessionId: string): Promise<SessionState> {
    return this.json("GET", `/sessions/${encodeURIComponent(sessionId)}`)
  }

  sendMessage(sessionId: string, content: string, signal?: AbortSignal): AsyncGenerator<AgentEvent> {
    return this.stream(`/sessions/${encodeURIComponent(sessionId)}/messages`, { content }, signal)
  }

  respondApproval(sessionId: string, approved: boolean, options: { feedback?: string; always?: boolean } = {}, signal?: AbortSignal) {
    const body = { approved, feedback: options.feedback ?? "", always: options.always ?? false }
    return this.stream(`/sessions/${encodeURIComponent(sessionId)}/approval`, body, signal)
  }

  async listSessions(limit = 20): Promise<SessionSummary[]> {
    return (await this.json<{ sessions: SessionSummary[] }>("GET", `/sessions?limit=${limit}`)).sessions
  }

  compact(sessionId: string): Promise<{ removed: number }> {
    return this.json("POST", `/sessions/${encodeURIComponent(sessionId)}/compact`)
  }

  undo(sessionId: string): Promise<UndoResult> {
    return this.json("POST", `/sessions/${encodeURIComponent(sessionId)}/undo`)
  }

  listModels(): Promise<{ default: string; models: string[]; providers: string[] }> {
    return this.json("GET", "/models")
  }

  setModel(sessionId: string, model: string): Promise<{ model: string }> {
    return this.json("POST", `/sessions/${encodeURIComponent(sessionId)}/model`, { model })
  }

  setMode(sessionId: string, mode: Mode): Promise<{ mode: Mode }> {
    return this.json("POST", `/sessions/${encodeURIComponent(sessionId)}/mode`, { mode })
  }

  getMemory(): Promise<MemoryState> {
    return this.json("GET", "/memory")
  }

  clearMemory(scope: MemoryScope): Promise<{ cleared: MemoryScope }> {
    return this.json("DELETE", `/memory?scope=${scope}`)
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return this.token ? { ...extra, Authorization: `Bearer ${this.token}` } : extra
  }

  private async request(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<Response> {
    let response: Response
    try {
      response = await fetch(new URL(path, this.baseUrl), {
        method,
        headers: this.headers(body === undefined ? {} : { "Content-Type": "application/json" }),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      })
    } catch (error) {
      if (signal?.aborted) throw error
      throw new AgentApiError(`Cannot reach the agent at ${this.baseUrl} (${(error as Error).message})`)
    }
    if (!response.ok) {
      let detail = response.statusText
      try {
        detail = ((await response.json()) as { detail?: string }).detail ?? detail
      } catch {}
      throw new AgentApiError(`${method} ${path} failed: ${response.status} ${detail}`, response.status)
    }
    return response
  }

  private async json<T>(method: string, path: string, body?: unknown): Promise<T> {
    return (await (await this.request(method, path, body)).json()) as T
  }

  private async *stream(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<AgentEvent> {
    const response = await this.request("POST", path, body, signal)
    if (!response.body) throw new AgentApiError("empty response stream")
    yield* parseSSE(response.body)
  }
}
