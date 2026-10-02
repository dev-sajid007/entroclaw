import { BoxRenderable, TextRenderable, type RenderContext } from "@opentui/core"

import type { AppState } from "../state/app-state.ts"
import { colors, faint, strong, styled, text } from "./theme.ts"

export function shortenPath(path: string, max = 50, home = process.env.HOME): string {
  const short = home && path.startsWith(home) ? `~${path.slice(home.length)}` : path
  return short.length > max ? `…${short.slice(short.length - max + 1)}` : short
}

export class Header {
  readonly view: BoxRenderable
  private readonly right: TextRenderable

  constructor(ctx: RenderContext) {
    this.view = new BoxRenderable(ctx, {
      flexDirection: "row",
      justifyContent: "space-between",
      border: ["bottom"],
      borderColor: colors.border,
      paddingX: 1,
      flexShrink: 0,
    })
    this.view.add(new TextRenderable(ctx, { content: styled([strong("⚡ Coding Agent", colors.accent)]), flexShrink: 0 }))
    this.right = new TextRenderable(ctx, { content: "", wrapMode: "none" })
    this.view.add(this.right)
  }

  update(state: AppState): void {
    const parts = []
    if (state.model) parts.push(faint(`${state.model}  `))
    parts.push(text(state.workspace ? shortenPath(state.workspace) : ""))
    this.right.content = styled(parts)
  }
}
