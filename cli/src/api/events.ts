// Event protocol streamed by the Python agent over SSE (see agent/src/coding_agent/server/events.py).

export interface Todo {
  content: string
  status: "pending" | "in_progress" | "completed"
}

export type ToolStatus = "running" | "success" | "error" | "rejected" | "denied"

export interface ApprovalRequest {
  type: "approval_required"
  tool_call_id: string
  action: string
  arguments: Record<string, unknown>
  risk: "sensitive" | "high"
  reason: string
  path?: string
  diff?: string
  interrupt_id?: string
  /** Whether "always allow" may be offered (false for high-risk actions). */
  allow_always?: boolean
  /** The session rule that "always allow" would add, e.g. "edit_file" or "run_command:npm test". */
  rule?: string
}

export type AgentEvent =
  | { type: "run_start"; session_id: string }
  | { type: "agent_token"; content: string }
  | { type: "agent_message"; content: string }
  | { type: "tool_start"; id: string; tool: string; args: Record<string, unknown> }
  | { type: "tool_output"; tool: string; content: string }
  | { type: "tool_end"; id: string; tool: string; status: Exclude<ToolStatus, "running">; result: string; duration?: number }
  | ApprovalRequest
  | { type: "usage"; input_tokens: number; output_tokens: number; total_tokens: number }
  | { type: "context_compacted"; removed: number; fallback: boolean }
  | { type: "todos"; todos: Todo[] }
  | { type: "final"; content: string }
  | { type: "error"; message: string }

/** Parse a `text/event-stream` body into events. Only `data:` fields are used; each holds one JSON event. */
export async function* parseSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<AgentEvent> {
  const decoder = new TextDecoder()
  let buffer = ""
  for await (const chunk of stream) {
    buffer += decoder.decode(chunk, { stream: true }).replace(/\r\n/g, "\n")
    let boundary = buffer.indexOf("\n\n")
    while (boundary !== -1) {
      const event = parseBlock(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      if (event) yield event
      boundary = buffer.indexOf("\n\n")
    }
  }
  buffer += decoder.decode()
  const tail = parseBlock(buffer)
  if (tail) yield tail
}

function parseBlock(block: string): AgentEvent | null {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n")
  if (!data) return null
  return JSON.parse(data) as AgentEvent
}
