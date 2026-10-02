// First-run setup: install the Python agent with uv (installing uv itself if needed), after asking.

import { existsSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

import { LaunchError, locateAgent } from "./agent.ts"
import { REPO } from "./paths.ts"
import { confirm } from "./prompt.ts"
import { VERSION } from "./version.ts"

const exe = (name: string) => (process.platform === "win32" ? `${name}.exe` : name)

/** What `uv tool install` gets: the wheel published with this exact release, unless overridden. */
export function agentInstallSpec(version = VERSION, env = process.env): string {
  return env.ENTROCLAW_AGENT_SPEC || `https://github.com/${REPO}/releases/download/v${version}/entroclaw_agent-${version}-py3-none-any.whl`
}

export function locateUv(): string | null {
  const candidates = [join(homedir(), ".local", "bin", exe("uv")), join(homedir(), ".cargo", "bin", exe("uv"))]
  return Bun.which("uv") ?? candidates.find((path) => existsSync(path)) ?? null
}

async function run(argv: string[]): Promise<number> {
  const proc = Bun.spawn(argv, { stdin: "inherit", stdout: "inherit", stderr: "inherit" })
  return proc.exited
}

export async function installUv(): Promise<string> {
  const code =
    process.platform === "win32"
      ? await run(["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-Command", "irm https://astral.sh/uv/install.ps1 | iex"])
      : await run(["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"])
  const uv = locateUv()
  if (code !== 0 || !uv) throw new LaunchError("Installing uv failed. Install it from https://docs.astral.sh/uv/ and try again.")
  return uv
}

export async function installAgent(uv: string, spec = agentInstallSpec()): Promise<void> {
  const code = await run([uv, "tool", "install", "--force", "--python", "3.12", spec])
  if (code !== 0) throw new LaunchError(`\`uv tool install ${spec}\` failed.`)
}

/** The agent command, installing it on first run when the user agrees. */
export async function ensureAgent(): Promise<string[]> {
  const found = locateAgent()
  if (found) return found
  const manual = `Install it with:\n  uv tool install --python 3.12 ${agentInstallSpec()}`
  if (!process.stdin.isTTY) throw new LaunchError(`The entroclaw agent is not installed.\n${manual}`)
  console.log("The entroclaw agent (Python) is not installed yet.")
  if (!(await confirm("Install it now with uv?"))) throw new LaunchError(manual)
  let uv = locateUv()
  if (!uv) {
    if (!(await confirm("uv (Python package manager from astral.sh) is required. Install it?"))) {
      throw new LaunchError("Install uv from https://docs.astral.sh/uv/, then run entroclaw again.")
    }
    uv = await installUv()
  }
  await installAgent(uv)
  const agent = locateAgent()
  if (!agent) throw new LaunchError(`The agent was installed but \`entroclaw-agent\` wasn't found. Add ~/.local/bin to PATH.`)
  return agent
}
