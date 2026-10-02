import type { TestRendererSetup } from "@opentui/core/testing"

/** Render until `text` is on screen. Markdown is parsed in a worker, so this needs wall-clock time, not just passes. */
export async function waitForText(setup: TestRendererSetup, text: string, timeoutMs = 5000): Promise<string> {
  const deadline = Date.now() + timeoutMs
  let frame = ""
  while (Date.now() < deadline) {
    await setup.renderOnce()
    frame = setup.captureCharFrame()
    if (frame.includes(text)) return frame
    await Bun.sleep(20)
  }
  throw new Error(`Timed out waiting for ${JSON.stringify(text)}. Last frame:\n${frame}`)
}

export interface AgentServer {
  url: string
  workspace: string
  stop: () => void
}

/**
 * Start the real Python agent (`coding-agent serve`) with a scripted model, in a fresh temp workspace
 * and state directory. `files` are written into the workspace first.
 */
export async function startAgentServer(script: unknown[], files: Record<string, string>): Promise<AgentServer> {
  const { mkdtempSync, mkdirSync, writeFileSync } = await import("node:fs")
  const { tmpdir } = await import("node:os")
  const { join, resolve } = await import("node:path")
  const { AgentClient } = await import("../src/api/agent.ts")

  const dir = mkdtempSync(join(tmpdir(), "coding-agent-e2e-"))
  const workspace = join(dir, "ws")
  mkdirSync(workspace)
  for (const [name, content] of Object.entries(files)) writeFileSync(join(workspace, name), content)
  writeFileSync(join(dir, "script.json"), JSON.stringify(script))
  const port = 20000 + Math.floor(Math.random() * 20000)
  const server = Bun.spawn(["uv", "run", "--quiet", "coding-agent", "serve"], {
    cwd: resolve(import.meta.dir, "../../agent"),
    env: {
      ...process.env,
      FAKE_MODEL_SCRIPT: join(dir, "script.json"),
      WORKSPACE: workspace,
      STATE_DIR: join(dir, "state"),
      PORT: String(port),
      LOG_LEVEL: "WARNING",
    },
    stdout: "ignore",
    stderr: "inherit",
  })
  const url = `http://127.0.0.1:${port}`
  const client = new AgentClient(url)
  for (let i = 0; i < 100; i++) {
    try {
      await client.health()
      return { url, workspace, stop: () => server.kill() }
    } catch {
      await Bun.sleep(200)
    }
  }
  server.kill()
  throw new Error("agent server did not start")
}
