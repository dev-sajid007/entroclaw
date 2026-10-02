#!/usr/bin/env node
// npm entry point: run the prebuilt entroclaw binary from the platform package npm installed.
"use strict"

const { spawnSync } = require("node:child_process")
const { existsSync, readFileSync } = require("node:fs")
const path = require("node:path")

function isMusl() {
  if (process.platform !== "linux") return false
  try {
    return readFileSync("/usr/bin/ldd", "utf8").includes("musl")
  } catch {
    return !process.report?.getReport?.().header?.glibcVersionRuntime
  }
}

function platformPackage() {
  const os = process.platform === "win32" ? "windows" : process.platform
  const arch = process.platform === "win32" && process.arch === "arm64" ? "x64" : process.arch
  return `entroclaw-${os}-${arch}${arch === "x64" && isMusl() ? "-musl" : ""}`
}

function binaryPath() {
  if (process.env.ENTROCLAW_BIN) return process.env.ENTROCLAW_BIN
  const name = platformPackage()
  const exe = process.platform === "win32" ? "entroclaw.exe" : "entroclaw"
  try {
    const dir = path.dirname(require.resolve(`${name}/package.json`))
    const file = path.join(dir, "bin", exe)
    if (existsSync(file)) return file
  } catch {}
  console.error(
    `entroclaw: no prebuilt binary for ${process.platform}-${process.arch} (expected the npm package "${name}").\n` +
      "If you installed with --no-optional or --omit=optional, reinstall without it, or use the install script:\n" +
      "  curl -fsSL https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.sh | sh",
  )
  process.exit(1)
}

const result = spawnSync(binaryPath(), process.argv.slice(2), { stdio: "inherit" })
if (result.error) {
  console.error(`entroclaw: ${result.error.message}`)
  process.exit(1)
}
process.exit(result.status ?? (result.signal ? 1 : 0))
