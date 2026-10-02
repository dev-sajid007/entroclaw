// The user's config file (`config.env`): dotenv lines, edited in place so comments survive.

import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"

export const KNOWN_KEYS: Record<string, string> = {
  OPENAI_API_KEY: "OpenAI API key",
  ANTHROPIC_API_KEY: "Anthropic API key",
  MODEL: 'default model, e.g. "anthropic:claude-opus-5-5" or "openai:gpt-4.1-mini"',
  MODELS: "comma-separated extra models offered by /model",
  MODEL_EFFORT: "Claude effort: low | medium | high | xhigh | max",
  OPENAI_BASE_URL: "OpenAI-compatible endpoint",
  REQUIRE_APPROVAL: "true | false",
  SANDBOX: "auto | bwrap | off",
  SANDBOX_NETWORK: "allow network inside the sandbox: true | false",
  TAVILY_API_KEY: "web_search via Tavily",
  BRAVE_SEARCH_API_KEY: "web_search via Brave",
  MCP_CONFIG: "path to an MCP servers JSON file",
}

const SECRET = /(KEY|TOKEN|SECRET|PASSWORD)/i
const LINE = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/

function unquote(value: string): string {
  const v = value.trim()
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) return v.slice(1, -1)
  return v.replace(/\s+#.*$/, "")
}

function quote(value: string): string {
  return /^[A-Za-z0-9_.:,/@+-]*$/.test(value) ? value : JSON.stringify(value)
}

export function parseEnv(text: string): Record<string, string> {
  const values: Record<string, string> = {}
  for (const line of text.split(/\r?\n/)) {
    const match = LINE.exec(line)
    if (match && !line.trimStart().startsWith("#")) values[match[1]!] = unquote(match[2]!)
  }
  return values
}

/** Set (or with `value === undefined`, remove) a key, keeping every other line as it was. */
export function updateEnv(text: string, key: string, value: string | undefined): string {
  const lines = text ? text.replace(/\r?\n$/, "").split(/\r?\n/) : []
  const index = lines.findIndex((line) => LINE.exec(line)?.[1] === key && !line.trimStart().startsWith("#"))
  if (value === undefined) {
    if (index >= 0) lines.splice(index, 1)
  } else if (index >= 0) {
    lines[index] = `${key}=${quote(value)}`
  } else {
    lines.push(`${key}=${quote(value)}`)
  }
  return lines.length ? `${lines.join("\n")}\n` : ""
}

export function mask(key: string, value: string): string {
  if (!SECRET.test(key) || !value) return value
  return value.length <= 8 ? "********" : `${value.slice(0, 4)}…${value.slice(-4)}`
}

export class ConfigFile {
  constructor(readonly path: string) {}

  read(): Record<string, string> {
    return existsSync(this.path) ? parseEnv(readFileSync(this.path, "utf8")) : {}
  }

  set(key: string, value: string | undefined): void {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) throw new Error(`invalid key: ${key}`)
    const current = existsSync(this.path) ? readFileSync(this.path, "utf8") : "# entroclaw configuration\n"
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 })
    writeFileSync(this.path, updateEnv(current, key, value), { mode: 0o600 })
    // writeFileSync's mode only applies on creation; tighten an existing file too (API keys live here).
    if (process.platform !== "win32") chmodSync(this.path, 0o600)
  }
}
