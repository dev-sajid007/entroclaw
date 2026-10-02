import { StyledText, SyntaxStyle, bold, dim, fg, type TextChunk } from "@opentui/core"

export const colors = {
  border: "#3b4261",
  accent: "#7aa2f7",
  user: "#9ece6a",
  agent: "#bb9af7",
  tool: "#7dcfff",
  text: "#c0caf5",
  muted: "#565f89",
  success: "#9ece6a",
  error: "#f7768e",
  warning: "#e0af68",
  added: "#9ece6a",
  removed: "#f7768e",
  hunk: "#7dcfff",
}

export type Chunks = TextChunk[]

export const text = (value: string, color = colors.text): TextChunk => fg(color)(value)
export const strong = (value: string, color = colors.text): TextChunk => bold(fg(color)(value))
export const faint = (value: string): TextChunk => dim(fg(colors.muted)(value))

export const styled = (chunks: Chunks): StyledText => new StyledText(chunks)

let markdownStyle: SyntaxStyle | undefined

/** Highlight groups for agent replies (markdown) and fenced code (tree-sitter capture names). */
export function getMarkdownStyle(): SyntaxStyle {
  markdownStyle ??= SyntaxStyle.fromStyles({
    default: { fg: colors.text },
    "markup.heading": { fg: colors.accent, bold: true },
    ...Object.fromEntries([1, 2, 3, 4, 5, 6].map((n) => [`markup.heading.${n}`, { fg: colors.accent, bold: true }])),
    "markup.strong": { bold: true },
    "markup.italic": { italic: true },
    "markup.strikethrough": { dim: true },
    "markup.raw": { fg: colors.warning },
    "markup.raw.block": { fg: colors.text },
    "markup.link": { fg: colors.tool, underline: true },
    "markup.link.label": { fg: colors.tool },
    "markup.link.url": { fg: colors.muted, underline: true },
    "markup.list": { fg: colors.agent },
    "markup.quote": { fg: colors.muted, italic: true },
    keyword: { fg: colors.agent },
    "keyword.function": { fg: colors.agent },
    "keyword.return": { fg: colors.agent },
    string: { fg: colors.added },
    number: { fg: colors.warning },
    boolean: { fg: colors.warning },
    comment: { fg: colors.muted, italic: true },
    function: { fg: colors.accent },
    "function.call": { fg: colors.accent },
    "function.method.call": { fg: colors.accent },
    type: { fg: colors.tool },
    "type.builtin": { fg: colors.tool },
    constant: { fg: colors.warning },
    operator: { fg: colors.hunk },
    property: { fg: colors.text },
  })
  return markdownStyle
}
