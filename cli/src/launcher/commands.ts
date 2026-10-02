// Non-TUI subcommands: auth, config, doctor, upgrade, and pass-through to the agent.

import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"

import { agentEnv, locateAgent } from "./agent.ts"
import { locateUv } from "./bootstrap.ts"
import { ConfigFile, KNOWN_KEYS, mask } from "./config.ts"
import { configFile, isCompiled, REPO, sourceAgentDir } from "./paths.ts"
import { ask, askSecret, confirm } from "./prompt.ts"
import { VERSION } from "./version.ts"

const PROVIDERS = [
  { name: "Anthropic (Claude)", key: "ANTHROPIC_API_KEY", model: "anthropic:claude-opus-5-5" },
  { name: "OpenAI", key: "OPENAI_API_KEY", model: "openai:gpt-4.1-mini" },
]

export async function auth(): Promise<number> {
  const config = new ConfigFile(configFile())
  console.log("Add an API key for entroclaw.\n")
  PROVIDERS.forEach((p, i) => console.log(`  ${i + 1}. ${p.name}`))
  console.log(`  ${PROVIDERS.length + 1}. Other (enter the environment variable name)`)
  const choice = Number(await ask("\nProvider [1]: ", "1"))
  let key: string
  let model: string | undefined
  if (choice >= 1 && choice <= PROVIDERS.length) {
    key = PROVIDERS[choice - 1]!.key
    model = PROVIDERS[choice - 1]!.model
  } else if (choice === PROVIDERS.length + 1) {
    key = (await ask("Variable name (e.g. GROQ_API_KEY): ")).toUpperCase()
  } else {
    console.error("Invalid choice.")
    return 1
  }
  const value = await askSecret(`${key}: `)
  if (!value) {
    console.error("No key entered; nothing changed.")
    return 1
  }
  config.set(key, value)
  const current = config.read().MODEL
  if (model && current !== model && (await confirm(`Use ${model} as the default model?`, !current))) config.set("MODEL", model)
  console.log(`\nSaved to ${config.path}`)
  console.log("Now run `entroclaw` inside a project.")
  return 0
}

export function configCommand(args: string[]): number {
  const config = new ConfigFile(configFile())
  const [action = "list", key, ...rest] = args
  switch (action) {
    case "path":
      console.log(config.path)
      return 0
    case "list": {
      const values = config.read()
      console.log(`# ${config.path}`)
      for (const [k, v] of Object.entries(values)) console.log(`${k}=${mask(k, v)}`)
      if (!Object.keys(values).length) console.log("(empty; run `entroclaw auth` or `entroclaw config set KEY VALUE`)")
      console.log("\nKnown keys:")
      for (const [k, description] of Object.entries(KNOWN_KEYS)) console.log(`  ${k.padEnd(22)} ${description}`)
      return 0
    }
    case "get":
      if (!key) return usage()
      console.log(config.read()[key] ?? "")
      return 0
    case "set":
      if (!key || !rest.length) return usage()
      config.set(key, rest.join(" "))
      console.log(`${key} saved to ${config.path}`)
      return 0
    case "unset":
      if (!key) return usage()
      config.set(key, undefined)
      console.log(`${key} removed`)
      return 0
    default:
      return usage()
  }
}

function usage(): number {
  console.error("Usage: entroclaw config [list | path | get KEY | set KEY VALUE | unset KEY]")
  return 2
}

/** `entroclaw run` / `entroclaw serve`: hand over to the Python agent in the current directory. */
export function passThrough(command: string, args: string[]): number {
  const agent = locateAgent()
  if (!agent) {
    console.error("The entroclaw agent is not installed. Run `entroclaw` once to set it up, or see `entroclaw doctor`.")
    return 1
  }
  const result = spawnSync(agent[0]!, [...agent.slice(1), command, ...args], { stdio: "inherit", env: agentEnv(process.cwd()) })
  return result.status ?? 1
}

function check(ok: boolean | "warn", label: string, detail = ""): boolean {
  const mark = ok === "warn" ? "!" : ok ? "✓" : "✗"
  console.log(`${mark} ${label}${detail ? `  ${detail}` : ""}`)
  return ok !== false
}

function versionOf(argv: string[]): string | null {
  const result = spawnSync(argv[0]!, [...argv.slice(1), "--version"], { encoding: "utf8", timeout: 60_000 })
  return result.status === 0 ? result.stdout.trim().split("\n").at(-1)! : null
}

export function doctor(): number {
  let healthy = true
  console.log(`entroclaw ${VERSION} (${process.platform}-${process.arch}${isCompiled() ? "" : ", from source"})\n`)
  const agent = locateAgent()
  const agentVersion = agent ? versionOf(agent) : null
  healthy = check(Boolean(agent), "agent", agent ? agent.join(" ") : "not installed: run `entroclaw` to install it") && healthy
  if (agent) {
    const matches = agentVersion === VERSION || sourceAgentDir() !== null
    check(matches ? true : "warn", "agent version", `${agentVersion ?? "unknown"}${matches ? "" : ` (expected ${VERSION}; run \`entroclaw upgrade\`)`}`)
  }
  check(locateUv() ? true : "warn", "uv", locateUv() ?? "not found (needed to install or upgrade the agent)")
  healthy = check(Boolean(Bun.which("git")), "git", Bun.which("git") ?? "not found (git tools won't work)") && healthy
  if (process.platform === "win32") {
    check(Bun.which("bash") ? true : "warn", "shell", Bun.which("bash") ? "Git Bash" : "PowerShell (install Git for Windows for bash)")
  } else {
    check(Boolean(Bun.which("bash")), "shell", Bun.which("bash") ?? "sh")
  }
  if (process.platform === "linux") {
    check(Bun.which("bwrap") ? true : "warn", "sandbox", Bun.which("bwrap") ? "bubblewrap" : "bubblewrap not installed: commands run unsandboxed")
  } else {
    check("warn", "sandbox", "only available on Linux: commands run unsandboxed")
  }
  const path = configFile()
  const values = new ConfigFile(path).read()
  const keys = Object.keys(values).filter((k) => k.endsWith("_API_KEY"))
  check(existsSync(path) ? true : "warn", "config", path)
  check(keys.length ? true : "warn", "API keys", keys.length ? keys.map((k) => `${k}=${mask(k, values[k]!)}`).join(", ") : "none: run `entroclaw auth`")
  check(true, "model", values.MODEL || "default (openai:gpt-4.1-mini)")
  return healthy ? 0 : 1
}

export function upgrade(): number {
  if (process.execPath.includes("node_modules") || process.argv.some((a) => a.includes("node_modules"))) {
    console.log("Installed with npm. Upgrade with:\n  npm install -g entroclaw@latest")
    return 0
  }
  if (!isCompiled()) {
    console.log("Running from source. Update with `git pull`, then `uv sync` and `bun install`.")
    return 0
  }
  const result =
    process.platform === "win32"
      ? spawnSync("powershell", ["-NoProfile", "-ExecutionPolicy", "ByPass", "-Command", `irm https://raw.githubusercontent.com/${REPO}/main/install.ps1 | iex`], { stdio: "inherit" })
      : spawnSync("sh", ["-c", `curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | sh`], { stdio: "inherit" })
  return result.status ?? 1
}
