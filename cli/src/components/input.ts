import { BoxRenderable, TextRenderable, TextareaRenderable, type KeyEvent, type RenderContext, type TextareaOptions } from "@opentui/core"

import type { PromptHistory } from "../state/history.ts"
import type { AppState } from "../state/app-state.ts"
import { colors, strong, styled } from "./theme.ts"

export const MAX_INPUT_LINES = 6

const KEY_BINDINGS: TextareaOptions["keyBindings"] = [
  { name: "return", action: "submit" },
  { name: "return", shift: true, action: "newline" },
  { name: "return", meta: true, action: "newline" },
  // Ctrl+J: most terminals can't distinguish Shift+Enter, but they all send a linefeed for Ctrl+J.
  { name: "linefeed", action: "newline" },
  { name: "j", ctrl: true, action: "newline" },
]

/** Textarea where Up on the first line / Down on the last line walk through prompt history. */
class PromptTextarea extends TextareaRenderable {
  history?: PromptHistory

  override handleKeyPress(key: KeyEvent): boolean {
    if (this.history && !key.ctrl && !key.meta && !key.shift) {
      const { row } = this.logicalCursor
      if (key.name === "up" && row === 0) {
        const entry = this.history.previous(this.plainText)
        if (entry !== null) this.setValue(entry)
        return true
      }
      if (key.name === "down" && row === this.lineCount - 1) {
        const entry = this.history.next()
        if (entry !== null) this.setValue(entry)
        return true
      }
    }
    return super.handleKeyPress(key)
  }

  setValue(value: string): void {
    this.setText(value)
    this.gotoBufferEnd()
  }
}

export class Input {
  readonly view: BoxRenderable
  readonly field: PromptTextarea

  constructor(ctx: RenderContext, onSubmit: (value: string) => void, history?: PromptHistory) {
    this.view = new BoxRenderable(ctx, {
      flexDirection: "row",
      border: ["top"],
      borderColor: colors.border,
      paddingX: 1,
      flexShrink: 0,
    })
    this.view.add(new TextRenderable(ctx, { content: styled([strong("> ", colors.accent)]), width: 2 }))
    this.field = new PromptTextarea(ctx, {
      flexGrow: 1,
      height: 1,
      wrapMode: "word",
      keyBindings: KEY_BINDINGS,
      placeholder: "Type a message…  (/help for commands)",
      onSubmit: () => {
        const value = this.field.plainText.trim()
        if (!value) return
        history?.add(value)
        this.field.setValue("")
        onSubmit(value)
      },
      onContentChange: () => this.fit(),
    })
    this.field.history = history
    this.view.add(this.field)
    this.field.focus()
  }

  /** Grow with the content, up to MAX_INPUT_LINES. */
  private fit(): void {
    const lines = Math.min(Math.max(this.field.lineCount, 1), MAX_INPUT_LINES)
    if (this.field.height !== lines) this.field.height = lines
  }

  get value(): string {
    return this.field.plainText
  }

  update(state: AppState): void {
    this.field.placeholder = state.pendingApproval
      ? state.pendingApproval.allow_always
        ? "y = allow · a = always allow · n = reject · or type feedback"
        : "y = allow · n = reject · or type feedback to reject with a note"
      : state.busy
        ? "Agent is working… (Esc to cancel)"
        : "Type a message…  (/help for commands)"
  }
}
