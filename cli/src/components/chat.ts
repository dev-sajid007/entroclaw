import { BoxRenderable, MarkdownRenderable, ScrollBoxRenderable, TextRenderable, type RenderContext, type Renderable } from "@opentui/core"

import type { ChatItem } from "../state/app-state.ts"
import { createApprovalView } from "./approval.ts"
import { colors, faint, getMarkdownStyle, strong, styled, text, type Chunks } from "./theme.ts"
import { toolChunks } from "./tool.ts"

type AgentItem = Extract<ChatItem, { kind: "agent" }>

function messageChunks(item: Exclude<ChatItem, AgentItem>): Chunks {
  switch (item.kind) {
    case "user":
      return [strong("You\n", colors.user), text(`> ${item.text}`)]
    case "error":
      return [strong("Error ", colors.error), text(item.text, colors.error)]
    case "info":
      return [faint(item.text)]
    case "tool":
      return toolChunks(item)
    case "approval":
      return []
  }
}

interface ItemView {
  item: ChatItem
  view: Renderable
  /** Update in place when the new item is the same kind; returns false if the view must be rebuilt. */
  update?: (next: ChatItem) => boolean
}

function createAgentView(ctx: RenderContext, item: AgentItem): ItemView {
  const box = new BoxRenderable(ctx, { flexDirection: "column", flexShrink: 0 })
  box.add(new TextRenderable(ctx, { content: styled([strong("Agent", colors.agent)]) }))
  const markdown = new MarkdownRenderable(ctx, {
    content: item.text,
    streaming: item.streaming,
    syntaxStyle: getMarkdownStyle(),
    fg: colors.text,
  })
  box.add(markdown)
  return {
    item,
    view: box,
    update: (next) => {
      if (next.kind !== "agent") return false
      // Streaming tokens only change the text, so reuse the renderable instead of rebuilding it per token.
      if (markdown.content !== next.text) markdown.content = next.text
      markdown.streaming = next.streaming
      return true
    },
  }
}

function createItemView(ctx: RenderContext, item: ChatItem): ItemView {
  if (item.kind === "agent") return createAgentView(ctx, item)
  if (item.kind === "approval") return { item, view: createApprovalView(ctx, item) }
  const box = new BoxRenderable(ctx, { flexDirection: "column", flexShrink: 0, paddingX: item.kind === "tool" ? 2 : 0 })
  box.add(new TextRenderable(ctx, { content: styled(messageChunks(item)), wrapMode: "word" }))
  return { item, view: box }
}

/**
 * Scrollable conversation. Items are only ever appended or replaced, so `sync` re-renders just the
 * items whose object identity changed (the reducer updates immutably).
 */
export class ChatView {
  readonly view: ScrollBoxRenderable
  private rendered: ItemView[] = []

  constructor(private readonly ctx: RenderContext) {
    this.view = new ScrollBoxRenderable(ctx, {
      flexGrow: 1,
      stickyScroll: true,
      stickyStart: "bottom",
      contentOptions: { flexDirection: "column", gap: 1, paddingX: 1 },
    })
  }

  sync(items: ChatItem[]): void {
    while (this.rendered.length > items.length) {
      const removed = this.rendered.pop()!
      this.view.remove(removed.view)
      removed.view.destroyRecursively()
    }
    items.forEach((item, index) => {
      const existing = this.rendered[index]
      if (existing?.item === item) return
      if (existing?.update?.(item)) {
        existing.item = item
        return
      }
      const next = createItemView(this.ctx, item)
      if (existing) {
        this.view.remove(existing.view)
        existing.view.destroyRecursively()
        this.view.add(next.view, index)
        this.rendered[index] = next
      } else {
        this.view.add(next.view)
        this.rendered.push(next)
      }
    })
  }

  scroll(lines: number): void {
    this.view.scrollBy(lines)
  }
}
