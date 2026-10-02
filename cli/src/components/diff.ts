import { colors, faint, text, type Chunks } from "./theme.ts"

export const MAX_DIFF_LINES = 400

/** Color a unified diff: additions green, removals red, hunk headers cyan, file headers dim. */
export function diffChunks(diff: string, maxLines = MAX_DIFF_LINES): Chunks {
  const lines = diff.replace(/\n$/, "").split("\n")
  const shown = lines.slice(0, maxLines)
  const chunks: Chunks = []
  shown.forEach((line, index) => {
    const newline = index < shown.length - 1 ? "\n" : ""
    if (line.startsWith("+++") || line.startsWith("---")) chunks.push(faint(line + newline))
    else if (line.startsWith("+")) chunks.push(text(line + newline, colors.added))
    else if (line.startsWith("-")) chunks.push(text(line + newline, colors.removed))
    else if (line.startsWith("@@")) chunks.push(text(line + newline, colors.hunk))
    else chunks.push(text(line + newline))
  })
  if (lines.length > maxLines) chunks.push(faint(`\n… ${lines.length - maxLines} more lines`))
  return chunks
}
