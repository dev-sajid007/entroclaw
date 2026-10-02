import { existsSync, statSync } from "node:fs"
import { resolve } from "node:path"
import { parseArgs } from "node:util"

import { createCliRenderer } from "@opentui/core"

import { AgentClient } from "./api/agent.ts"
import { App } from "./app.ts"
import { LaunchError, startAgentServer, type AgentServer } from "./launcher/agent.ts"
import { ensureAgent } from "./launcher/bootstrap.ts"
import { auth, configCommand, doctor, passThrough, upgrade } from "./launcher/commands.ts"
import { sourceAgentDir } from "./launcher/paths.ts"
import { VERSION } from "./launcher/version.ts"
import { addNotice } from "./state/app-state.ts"
import { PromptHistory } from "./state/history.ts"

const HELP = `entroclaw ${VERSION}: AI coding agent for your terminal

Usage:
  entroclaw [dir]                  start in dir (default: current directory)
    -c, --continue                 resume the latest session in this directory
    --session <id>                 resume a specific session
  entroclaw auth                   add an API key (OpenAI, Anthropic, …)
  entroclaw config [list|path|get|set|unset]   manage settings
  entroclaw run "<prompt>" [--yes] run one task headlessly in the current directory
  entroclaw serve                  run only the agent API (for \`entroclaw attach\`)
  entroclaw attach --url <url> [--token <t>]   connect the UI to a running agent
  entroclaw doctor                 check the installation
  entroclaw upgrade                update to the latest release
  entroclaw --version`

const SUBCOMMANDS = new Set(["auth", "config", "run", "serve", "attach", "doctor", "upgrade", "help"])

async function runTui(client: AgentClient, options: { session?: string; continueLatest?: boolean }, server?: AgentServer) {
  const history = new PromptHistory()
  await history.load()
  const renderer = await createCliRenderer({ exitOnCtrlC: true })
  renderer.setTerminalTitle("entroclaw")
  let closing = false
  const shutdown = async (code = 0) => {
    if (closing) return
    closing = true
    app.dispose()
    renderer.destroy()
    await server?.stop()
    process.exit(code)
  }
  const app = new App(renderer, client, () => void shutdown(), history)
  // exitOnCtrlC destroys the renderer; make sure the agent server goes with it.
  renderer.on("destroy", () => void shutdown())
  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"] as const) process.on(signal, () => void shutdown(130))
  await app.start(options.session, { continueLatest: options.continueLatest })
  if (server?.version && server.version !== VERSION && !sourceAgentDir()) {
    app.store.update((s) => addNotice(s, `The agent is version ${server.version} but entroclaw is ${VERSION}. Run \`entroclaw upgrade\`.`, "error"))
  }
}

async function main(argv: string[]): Promise<number> {
  const [first, ...rest] = argv
  if (first === "--version" || first === "-v") {
    console.log(VERSION)
    return 0
  }
  if (first === "--help" || first === "-h" || first === "help") {
    console.log(HELP)
    return 0
  }
  if (first && SUBCOMMANDS.has(first)) {
    switch (first) {
      case "auth":
        return auth()
      case "config":
        return configCommand(rest)
      case "doctor":
        return doctor()
      case "upgrade":
        return upgrade()
      case "run":
      case "serve":
        return passThrough(first, rest)
      case "attach": {
        const { values } = parseArgs({
          args: rest,
          options: {
            url: { type: "string", default: process.env.AGENT_URL ?? "http://127.0.0.1:8765" },
            token: { type: "string", default: process.env.AGENT_API_TOKEN },
            session: { type: "string" },
            continue: { type: "boolean", short: "c" },
          },
        })
        await runTui(new AgentClient(values.url!, values.token), { session: values.session, continueLatest: values.continue })
        return -1 // the TUI owns the process from here
      }
    }
  }

  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: { session: { type: "string" }, continue: { type: "boolean", short: "c" } },
  })
  const workspace = resolve(positionals[0] ?? process.cwd())
  if (!existsSync(workspace) || !statSync(workspace).isDirectory()) {
    console.error(`Not a directory: ${workspace}`)
    return 1
  }
  const agent = await ensureAgent()
  process.stdout.write("Starting entroclaw…\r")
  const server = await startAgentServer({ workspace, agent })
  await runTui(new AgentClient(server.url, server.token), { session: values.session, continueLatest: values.continue }, server)
  return -1
}

try {
  const code = await main(process.argv.slice(2))
  if (code >= 0) process.exit(code)
} catch (error) {
  if (error instanceof LaunchError || (error as Error).message === "cancelled") {
    console.error(`\n${(error as Error).message}`)
    process.exit(1)
  }
  if ((error as { code?: string }).code?.startsWith("ERR_PARSE_ARGS")) {
    console.error(`${(error as Error).message}\n\n${HELP}`)
    process.exit(2)
  }
  throw error
}
