import { parseArgs } from "node:util"

import { createCliRenderer } from "@opentui/core"

import { AgentClient } from "./api/agent.ts"
import { App } from "./app.ts"
import { PromptHistory } from "./state/history.ts"

const { values } = parseArgs({
  options: {
    url: { type: "string", default: process.env.AGENT_URL ?? "http://127.0.0.1:8765" },
    session: { type: "string" },
    continue: { type: "boolean", short: "c" },
    token: { type: "string", default: process.env.AGENT_API_TOKEN },
    help: { type: "boolean", short: "h" },
  },
})

if (values.help) {
  console.log(`Usage: bun run src/main.ts [--url URL] [--session ID] [--token TOKEN]

  --url      agent API base URL (env AGENT_URL, default http://127.0.0.1:8765)
  --session  resume an existing session
  -c, --continue  resume the most recent session in this workspace
  --token    bearer token if the agent sets AGENT_API_TOKEN (env AGENT_API_TOKEN)`)
  process.exit(0)
}

const history = new PromptHistory()
await history.load()
const renderer = await createCliRenderer({ exitOnCtrlC: true })
renderer.setTerminalTitle("Coding Agent")
const app = new App(renderer, new AgentClient(values.url!, values.token), () => {
  app.dispose()
  renderer.destroy()
  process.exit(0)
}, history)
renderer.on("destroy", () => app.dispose())
await app.start(values.session, { continueLatest: values.continue })
