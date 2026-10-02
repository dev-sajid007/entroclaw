import { BoxRenderable, TextRenderable, type RenderContext } from "@opentui/core"

import type { ChatItem } from "../state/app-state.ts"
import { diffChunks } from "./diff.ts"
import { colors, faint, strong, styled, text, type Chunks } from "./theme.ts"

type ApprovalItem = Extract<ChatItem, { kind: "approval" }>

/** Human-readable form of a session rule: "run_command:npm test" → "`npm test`". */
export function ruleLabel(rule: string): string {
  return rule.startsWith("run_command:") ? `\`${rule.slice("run_command:".length)}\`` : rule
}

export function approvalChunks(item: ApprovalItem): Chunks {
  const { request, decision } = item
  const chunks: Chunks = [strong(request.action, colors.tool), text(` — ${request.reason}`)]
  if (request.risk === "high") chunks.push(strong("  HIGH RISK", colors.error))
  if (request.diff) {
    chunks.push(text("\n\n"), ...diffChunks(request.diff))
  } else {
    const command = request.arguments.command
    const details = typeof command === "string" ? `$ ${command}` : JSON.stringify(request.arguments, null, 2)
    chunks.push(text(`\n\n${details}`))
  }
  if (decision === "approved") chunks.push(text("\n\n✓ Allowed", colors.success))
  else if (decision === "always") chunks.push(text(`\n\n✓ Allowed — always for ${ruleLabel(request.rule ?? request.action)} this session`, colors.success))
  else if (decision === "rejected") chunks.push(text("\n\n⊘ Rejected", colors.warning))
  else {
    chunks.push(text("\n\n"), strong("[Y] Allow", colors.success), text("   "))
    if (request.allow_always) chunks.push(strong("[A] Always allow", colors.accent), faint(` ${ruleLabel(request.rule ?? request.action)}`), text("   "))
    chunks.push(strong("[N] Reject", colors.error), faint("   or type feedback to reject with a note"))
  }
  return chunks
}

export function createApprovalView(ctx: RenderContext, item: ApprovalItem): BoxRenderable {
  const pending = !item.decision
  const box = new BoxRenderable(ctx, {
    border: true,
    borderStyle: "rounded",
    borderColor: pending ? (item.request.risk === "high" ? colors.error : colors.warning) : colors.border,
    title: " Approval Required ",
    titleAlignment: "center",
    paddingX: 1,
    flexDirection: "column",
    flexShrink: 0,
  })
  box.add(new TextRenderable(ctx, { content: styled(approvalChunks(item)), wrapMode: "char" }))
  return box
}
