import type { ChatItem } from "../state/app-state.ts"
import { colors, faint, strong, text, type Chunks } from "./theme.ts"

type ToolItem = Extract<ChatItem, { kind: "tool" }>

const STATUS_ICON: Record<ToolItem["status"], [string, string]> = {
  running: ["◌", colors.warning],
  success: ["✓", colors.success],
  error: ["✗", colors.error],
  rejected: ["⊘", colors.warning],
  denied: ["⛔", colors.error],
}

const PREVIEW_LINES = 12

/** One-line summary of tool arguments, e.g. read_file("src/app.py"). */
export function formatCall(tool: string, args: Record<string, unknown>): string {
  const primary = args.command ?? args.path ?? args.pattern ?? args.message ?? args.revision
  if (typeof primary === "string") return `${tool}(${JSON.stringify(truncateLine(primary, 80))})`
  const rest = Object.keys(args).length ? truncateLine(JSON.stringify(args), 80) : ""
  return `${tool}(${rest})`
}

function truncateLine(value: string, max: number): string {
  const line = value.split("\n")[0] ?? ""
  return line.length > max || value.includes("\n") ? `${line.slice(0, max)}…` : line
}

function tail(value: string, lines: number): string {
  const all = value.replace(/\n$/, "").split("\n")
  if (all.length <= lines) return all.join("\n")
  return [`… ${all.length - lines} earlier lines`, ...all.slice(-lines)].join("\n")
}

export function toolChunks(item: ToolItem): Chunks {
  const [icon, color] = STATUS_ICON[item.status]
  const chunks: Chunks = [text(`${icon} `, color), strong("Tool ", colors.tool), text(formatCall(item.tool, item.args))]
  if (item.duration !== undefined) chunks.push(faint(`  ${item.duration.toFixed(1)}s`))
  if (item.status === "running" && item.output) {
    chunks.push(faint(`\n${tail(item.output, PREVIEW_LINES)}`))
  } else if (item.status !== "running" && item.status !== "success" && item.result) {
    chunks.push(text(`\n${tail(item.result, PREVIEW_LINES)}`, item.status === "error" || item.status === "denied" ? colors.error : colors.warning))
  } else if (item.status === "success" && item.tool === "run_command" && item.result) {
    chunks.push(faint(`\n${tail(item.result, PREVIEW_LINES)}`))
  }
  return chunks
}
