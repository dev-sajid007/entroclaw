// Locate, start and stop the Python agent server that the TUI talks to.

import { closeSync, existsSync, mkdirSync, openSync, readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import type { Subprocess } from "bun"

import { configFile, sourceAgentDir, stateDir } from "./paths.ts"

export const AGENT_COMMAND = "entroclaw-agent"
const HEALTH_TIMEOUT_MS = 30_000

export class LaunchError extends Error {}

/** Command prefix that runs the agent, or null if it isn't installed. */
export function locateAgent(env: Record<string, string | undefined> = process.env): string[] | null {
  if (env.ENTROCLAW_AGENT) return env.ENTROCLAW_AGENT.split(" ").filter(Boolean)
  const source = sourceAgentDir()
  if (source && Bun.which("uv")) return ["uv", "run", "--quiet", "--project", source, AGENT_COMMAND]
  const onPath = Bun.which(AGENT_COMMAND)
  if (onPath) return [onPath]
  // uv's default tool bin dir, in case it isn't on PATH yet (fresh install, same shell).
  const exe = process.platform === "win32" ? `${AGENT_COMMAND}.exe` : AGENT_COMMAND
  const uvBin = join(env.UV_TOOL_BIN_DIR || join(env.XDG_BIN_HOME || join(homedir(), ".local", "bin")), exe)
  return existsSync(uvBin) ? [uvBin] : null
}

/** Environment for any agent process: the user's config file, never a project .env. */
export function agentEnv(workspace: string, extra: Record<string, string> = {}): Record<string, string> {
  return {
    ...(process.env as Record<string, string>),
    AGENT_ENV_FILE: process.env.AGENT_ENV_FILE || configFile(),
    WORKSPACE: workspace,
    ...extra,
  }
}

export function freePort(): number {
  const listener = Bun.listen({ hostname: "127.0.0.1", port: 0, socket: { data() {} } })
  const port = listener.port
  listener.stop(true)
  return port
}

function randomToken(): string {
  return Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64url")
}

export function explainFailure(log: string): string {
  const tail = log.trim().split("\n").slice(-15).join("\n")
  if (/API_KEY is not set/.test(log)) {
    return `No API key is configured for the selected model.\nRun \`entroclaw auth\` to add one.\n\n${tail}`
  }
  return `The agent failed to start. Last log lines:\n\n${tail}`
}

export interface AgentServer {
  url: string
  token: string
  logPath: string
  version?: string
  stop(): Promise<void>
}

export interface StartOptions {
  workspace: string
  agent: string[]
  env?: Record<string, string>
  logDir?: string
  timeoutMs?: number
}

export async function startAgentServer(options: StartOptions): Promise<AgentServer> {
  const port = freePort()
  const token = randomToken()
  const logDir = options.logDir ?? stateDir()
  mkdirSync(logDir, { recursive: true })
  const logPath = join(logDir, "server.log")
  const logFd = openSync(logPath, "a")
  const logStart = existsSync(logPath) ? readFileSync(logPath).length : 0

  let proc: Subprocess
  try {
    proc = Bun.spawn([...options.agent, "serve"], {
      cwd: options.workspace,
      env: agentEnv(options.workspace, { HOST: "127.0.0.1", PORT: String(port), AGENT_API_TOKEN: token, ...options.env }),
      stdin: "ignore",
      stdout: logFd,
      stderr: logFd,
    })
  } catch (error) {
    closeSync(logFd)
    throw new LaunchError(`Could not start ${options.agent.join(" ")}: ${(error as Error).message}`)
  }

  const url = `http://127.0.0.1:${port}`
  let stopped = false
  const stop = async () => {
    if (stopped) return
    stopped = true
    proc.kill("SIGTERM")
    const exited = await Promise.race([proc.exited.then(() => true), Bun.sleep(3000).then(() => false)])
    if (!exited) proc.kill("SIGKILL")
    closeSync(logFd)
  }

  const deadline = Date.now() + (options.timeoutMs ?? HEALTH_TIMEOUT_MS)
  while (Date.now() < deadline) {
    if (proc.exitCode !== null) break
    try {
      const response = await fetch(`${url}/health`, { headers: { Authorization: `Bearer ${token}` } })
      if (response.ok) {
        const health = (await response.json()) as { version?: string }
        return { url, token, logPath, version: health.version, stop }
      }
    } catch {
      // not listening yet
    }
    await Bun.sleep(150)
  }
  await stop()
  const log = readFileSync(logPath).subarray(logStart).toString("utf8")
  throw new LaunchError(proc.exitCode !== null ? explainFailure(log) : `The agent did not become ready in time. See ${logPath}`)
}
