import { BoxRenderable, TextRenderable, type RenderContext } from "@opentui/core"

import type { AppState } from "../state/app-state.ts"
import { colors, faint, styled, text } from "./theme.ts"

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

export class StatusBar {
  readonly view: BoxRenderable
  private readonly left: TextRenderable
  private readonly right: TextRenderable
  private frame = 0

  constructor(ctx: RenderContext) {
    this.view = new BoxRenderable(ctx, { flexDirection: "row", justifyContent: "space-between", paddingX: 1, flexShrink: 0, height: 1 })
    this.left = new TextRenderable(ctx, { content: "" })
    this.right = new TextRenderable(ctx, { content: "" })
    this.view.add(this.left)
    this.view.add(this.right)
  }

  tick(): void {
    this.frame = (this.frame + 1) % SPINNER.length
  }

  update(state: AppState): void {
    const dot =
      state.connection === "connected"
        ? text("● ", colors.success)
        : state.connection === "connecting"
          ? text("● ", colors.warning)
          : text("● ", colors.error)
    const spinner = state.busy ? text(`${SPINNER[this.frame]} `, colors.accent) : text("")
    const statusColor = state.pendingApproval ? colors.warning : state.status === "Error" ? colors.error : colors.text
    this.left.content = styled([dot, spinner, text(state.status, statusColor)])

    const right = []
    const tokens = state.tokens.input + state.tokens.output
    if (tokens) right.push(faint(`${tokens.toLocaleString()} tok  `))
    if (state.sessionId) right.push(faint(`session ${state.sessionId.slice(0, 8)}  `))
    right.push(faint(state.busy ? "Esc cancel  Ctrl+C exit" : "Ctrl+J newline  ↑ history  PgUp/PgDn scroll"))
    this.right.content = styled(right)
  }
}
