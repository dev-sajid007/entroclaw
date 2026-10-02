import { describe, expect, test } from "bun:test"
import { mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

import { explainFailure, LaunchError, locateAgent, startAgentServer } from "../src/launcher/agent.ts"
import { agentInstallSpec } from "../src/launcher/bootstrap.ts"
import { ConfigFile, mask, parseEnv, updateEnv } from "../src/launcher/config.ts"
import { configDir, stateDir } from "../src/launcher/paths.ts"
import { VERSION } from "../src/launcher/version.ts"

const AGENT_DIR = resolve(import.meta.dir, "../../agent")
const MAIN = resolve(import.meta.dir, "../src/main.ts")
const tmp = () => mkdtempSync(join(tmpdir(), "entroclaw-test-"))

describe("config file", () => {
  test("parse and update keep comments and order", () => {
    const text = '# my settings\nMODEL=openai:gpt-4.1-mini\nexport OPENAI_API_KEY="sk-abc"\n# MODEL=commented\n'
    expect(parseEnv(text)).toEqual({ MODEL: "openai:gpt-4.1-mini", OPENAI_API_KEY: "sk-abc" })
    const updated = updateEnv(text, "MODEL", "anthropic:claude-opus-5-5")
    expect(updated).toBe('# my settings\nMODEL=anthropic:claude-opus-5-5\nexport OPENAI_API_KEY="sk-abc"\n# MODEL=commented\n')
    expect(updateEnv(updated, "OPENAI_API_KEY", undefined)).toBe("# my settings\nMODEL=anthropic:claude-opus-5-5\n# MODEL=commented\n")
    expect(updateEnv("", "NOTE", "has spaces # and hash")).toBe('NOTE="has spaces # and hash"\n')
    expect(parseEnv('NOTE="has spaces # and hash"\nX=1 # comment')).toEqual({ NOTE: "has spaces # and hash", X: "1" })
  })

  test("file is private and values are masked", () => {
    const file = new ConfigFile(join(tmp(), "nested", "config.env"))
    file.set("ANTHROPIC_API_KEY", "sk-ant-1234567890")
    file.set("MODEL", "anthropic:claude-opus-5-5")
    expect(file.read()).toEqual({ ANTHROPIC_API_KEY: "sk-ant-1234567890", MODEL: "anthropic:claude-opus-5-5" })
    if (process.platform !== "win32") expect(statSync(file.path).mode & 0o777).toBe(0o600)
    expect(mask("ANTHROPIC_API_KEY", "sk-ant-1234567890")).toBe("sk-a…7890")
    expect(mask("MODEL", "anthropic:claude-opus-5-5")).toBe("anthropic:claude-opus-5-5")
    expect(() => file.set("BAD KEY", "x")).toThrow("invalid key")
  })
})

test("paths per platform", () => {
  const env = { APPDATA: "C:\\Users\\a\\AppData\\Roaming", LOCALAPPDATA: "C:\\Users\\a\\AppData\\Local" }
  expect(configDir("win32", env, "C:\\Users\\a")).toBe(join(env.APPDATA, "entroclaw"))
  expect(stateDir("win32", env, "C:\\Users\\a")).toBe(join(env.LOCALAPPDATA, "entroclaw"))
  expect(configDir("linux", {}, "/home/a")).toBe("/home/a/.config/entroclaw")
  expect(stateDir("darwin", { XDG_STATE_HOME: "/x" }, "/home/a")).toBe("/x/entroclaw")
})

test("agent location and install spec", () => {
  expect(locateAgent({ ENTROCLAW_AGENT: "/opt/agent --flag" })).toEqual(["/opt/agent", "--flag"])
  // From this source checkout, the agent runs through uv.
  expect(locateAgent({})).toEqual(["uv", "run", "--quiet", "--project", AGENT_DIR, "entroclaw-agent"])
  expect(agentInstallSpec("1.2.3", {})).toBe(
    "https://github.com/dev-sajid007/entroclaw/releases/download/v1.2.3/entroclaw_agent-1.2.3-py3-none-any.whl",
  )
  expect(agentInstallSpec("1.2.3", { ENTROCLAW_AGENT_SPEC: "./agent" })).toBe("./agent")
})

test("missing API key explains how to fix it", () => {
  const message = explainFailure("...\nentroclaw-agent: OPENAI_API_KEY is not set (needed for openai:gpt-4.1-mini).\n")
  expect(message).toContain("Run `entroclaw auth`")
})

describe("server lifecycle (real agent)", () => {
  const agent = ["uv", "run", "--quiet", "--project", AGENT_DIR, "entroclaw-agent"]

  test("starts with a private token, serves the workspace, and stops", async () => {
    const dir = tmp()
    const workspace = join(dir, "ws")
    await Bun.$`mkdir -p ${workspace}`
    const script = join(dir, "script.json")
    writeFileSync(script, JSON.stringify([{ content: "hi" }]))
    const env = { FAKE_MODEL_SCRIPT: script, STATE_DIR: join(dir, "state"), AGENT_ENV_FILE: join(dir, "none.env") }
    const server = await startAgentServer({ workspace, agent, env, logDir: dir })
    try {
      expect((await fetch(`${server.url}/health`)).status).toBe(401)
      const response = await fetch(`${server.url}/health`, { headers: { Authorization: `Bearer ${server.token}` } })
      const health = (await response.json()) as { workspace: string }
      expect(health.workspace).toBe(workspace)
      expect(server.version).toBe(VERSION)
    } finally {
      await server.stop()
    }
    await expect(fetch(`${server.url}/health`)).rejects.toThrow()
  }, 60_000)

  test("early exit without a key is reported with the fix", async () => {
    const dir = tmp()
    const env = { OPENAI_API_KEY: "", STATE_DIR: join(dir, "state"), AGENT_ENV_FILE: join(dir, "none.env"), MODEL: "openai:gpt-4.1-mini" }
    const error = await startAgentServer({ workspace: dir, agent, env, logDir: dir }).catch((e) => e)
    expect(error).toBeInstanceOf(LaunchError)
    expect(error.message).toContain("entroclaw auth")
    expect(readFileSync(join(dir, "server.log"), "utf8")).toContain("OPENAI_API_KEY is not set")
  }, 60_000)
})

describe("command line", () => {
  const run = (args: string[], env: Record<string, string> = {}) => {
    const result = Bun.spawnSync(["bun", "run", MAIN, ...args], { env: { ...process.env, ...env }, stdout: "pipe", stderr: "pipe" })
    return { code: result.exitCode, out: result.stdout.toString(), err: result.stderr.toString() }
  }

  test("--version and --help", () => {
    expect(run(["--version"]).out.trim()).toBe(VERSION)
    const help = run(["--help"]).out
    expect(help).toContain("entroclaw auth")
    expect(help).toContain("entroclaw doctor")
  })

  test("config set/get/unset use the user config dir", () => {
    const env = { XDG_CONFIG_HOME: tmp() }
    expect(run(["config", "set", "MODEL", "anthropic:claude-opus-5-5"], env).code).toBe(0)
    expect(run(["config", "get", "MODEL"], env).out.trim()).toBe("anthropic:claude-opus-5-5")
    expect(run(["config", "path"], env).out.trim()).toBe(join(env.XDG_CONFIG_HOME, "entroclaw", "config.env"))
    run(["config", "unset", "MODEL"], env)
    expect(run(["config", "get", "MODEL"], env).out.trim()).toBe("")
    expect(run(["config", "bogus"], env).code).toBe(2)
  })

  test("headless run passes through to the agent in the current directory", () => {
    const dir = tmp()
    const script = join(dir, "script.json")
    writeFileSync(script, JSON.stringify([{ tool_calls: [{ name: "list_files", args: {} }] }, { content: "Listed." }]))
    const result = Bun.spawnSync(["bun", "run", MAIN, "run", "list files"], {
      cwd: dir,
      env: { ...process.env, FAKE_MODEL_SCRIPT: script, STATE_DIR: join(dir, "state"), AGENT_ENV_FILE: join(dir, "none.env") },
      stdout: "pipe",
      stderr: "pipe",
    })
    expect(result.exitCode).toBe(0)
    expect(result.stdout.toString()).toContain("Listed.")
  }, 60_000)

  test("rejects a directory that doesn't exist", () => {
    const result = run([join(tmp(), "missing")])
    expect(result.code).toBe(1)
    expect(result.err).toContain("Not a directory")
  })
})
