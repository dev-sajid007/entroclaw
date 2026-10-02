import { BoxRenderable, TextRenderable, type RenderContext } from "@opentui/core"

import type { Todo } from "../api/events.ts"
import type { AppState } from "../state/app-state.ts"
import { colors, faint, strong, styled, text, type Chunks } from "./theme.ts"

const ICON: Record<Todo["status"], [string, string]> = {
  pending: ["☐", colors.muted],
  in_progress: ["◐", colors.warning],
  completed: ["☑", colors.success],
}

export function todoChunks(todos: Todo[]): Chunks {
  const done = todos.filter((t) => t.status === "completed").length
  const chunks: Chunks = [strong("Tasks ", colors.accent), faint(`${done}/${todos.length}`)]
  for (const todo of todos) {
    const [icon, color] = ICON[todo.status]
    chunks.push(text(`\n${icon} `, color))
    if (todo.status === "completed") chunks.push(faint(todo.content))
    else chunks.push(todo.status === "in_progress" ? strong(todo.content) : text(todo.content))
  }
  return chunks
}

/** The agent's checklist, shown above the input while it has items. */
export class TodoPanel {
  readonly view: BoxRenderable
  private readonly body: TextRenderable
  private current: Todo[] | undefined

  constructor(ctx: RenderContext) {
    this.view = new BoxRenderable(ctx, { flexDirection: "column", border: ["top"], borderColor: colors.border, paddingX: 1, flexShrink: 0 })
    this.body = new TextRenderable(ctx, { content: "", wrapMode: "word" })
    this.view.add(this.body)
    this.view.visible = false
  }

  update(state: AppState): void {
    if (state.todos === this.current) return
    this.current = state.todos
    this.view.visible = state.todos.length > 0
    if (state.todos.length) this.body.content = styled(todoChunks(state.todos))
  }
}
