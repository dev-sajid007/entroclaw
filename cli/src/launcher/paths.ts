// Per-OS locations shared with the Python agent (see agent/src/coding_agent/config/settings.py).

import { existsSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve } from "node:path"

export const APP = "entroclaw"
export const REPO = "dev-sajid007/entroclaw"

type Env = Record<string, string | undefined>

export function configDir(platform: string = process.platform, env: Env = process.env, home = homedir()): string {
  if (platform === "win32") return join(env.APPDATA || join(home, "AppData", "Roaming"), APP)
  return join(env.XDG_CONFIG_HOME || join(home, ".config"), APP)
}

export function stateDir(platform: string = process.platform, env: Env = process.env, home = homedir()): string {
  if (platform === "win32") return join(env.LOCALAPPDATA || join(home, "AppData", "Local"), APP)
  return join(env.XDG_STATE_HOME || join(home, ".local", "state"), APP)
}

export function configFile(platform?: string, env?: Env, home?: string): string {
  return join(configDir(platform, env, home), "config.env")
}

/** Where install.sh / install.ps1 put the binary. */
export function installBinDir(home = homedir()): string {
  return join(home, `.${APP}`, "bin")
}

/** True when running as a compiled single-file binary (not `bun run src/main.ts`). */
export function isCompiled(): boolean {
  return import.meta.dir.startsWith("/$bunfs") || import.meta.dir.includes("~BUN")
}

/** The agent/ directory when running from a source checkout, so development needs no install. */
export function sourceAgentDir(): string | null {
  if (isCompiled()) return null
  const candidate = resolve(dirname(import.meta.dir), "..", "..", "agent")
  return existsSync(join(candidate, "pyproject.toml")) ? candidate : null
}
