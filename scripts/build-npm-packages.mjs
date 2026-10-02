#!/usr/bin/env node
// Build the npm packages for a release.
//   node scripts/build-npm-packages.mjs <version> <binaries-dir> <out-dir>
// <binaries-dir> holds one folder per platform (e.g. linux-x64/entroclaw, windows-x64/entroclaw.exe).
// Produces <out-dir>/entroclaw-<platform>/ (binary only) and <out-dir>/entroclaw/ (the launcher wrapper).
import { chmodSync, cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const PLATFORMS = {
  "linux-x64": { os: "linux", cpu: "x64", libc: "glibc" },
  "linux-x64-musl": { os: "linux", cpu: "x64", libc: "musl" },
  "linux-arm64": { os: "linux", cpu: "arm64", libc: "glibc" },
  "darwin-x64": { os: "darwin", cpu: "x64" },
  "darwin-arm64": { os: "darwin", cpu: "arm64" },
  "windows-x64": { os: "win32", cpu: "x64" },
}

const [version, binaries, out] = process.argv.slice(2)
if (!version || !binaries || !out) {
  console.error("usage: build-npm-packages.mjs <version> <binaries-dir> <out-dir>")
  process.exit(2)
}
const root = join(dirname(fileURLToPath(import.meta.url)), "..")
const repository = { type: "git", url: "git+https://github.com/dev-sajid007/entroclaw.git" }

const built = []
for (const [platform, target] of Object.entries(PLATFORMS)) {
  const exe = target.os === "win32" ? "entroclaw.exe" : "entroclaw"
  const source = join(binaries, platform, exe)
  if (!existsSync(source)) continue
  const dir = join(out, `entroclaw-${platform}`)
  mkdirSync(join(dir, "bin"), { recursive: true })
  cpSync(source, join(dir, "bin", exe))
  if (target.os !== "win32") chmodSync(join(dir, "bin", exe), 0o755)
  const pkg = {
    name: `entroclaw-${platform}`,
    version,
    description: `Prebuilt entroclaw binary for ${platform}`,
    repository,
    license: "MIT",
    os: [target.os],
    cpu: [target.cpu],
    ...(target.libc ? { libc: [target.libc] } : {}),
    files: ["bin/"],
  }
  writeFileSync(join(dir, "package.json"), `${JSON.stringify(pkg, null, 2)}\n`)
  built.push(platform)
}

const wrapper = join(out, "entroclaw")
cpSync(join(root, "npm", "entroclaw"), wrapper, { recursive: true })
const pkg = JSON.parse(readFileSync(join(wrapper, "package.json"), "utf8"))
pkg.version = version
pkg.optionalDependencies = Object.fromEntries(Object.keys(PLATFORMS).map((p) => [`entroclaw-${p}`, version]))
writeFileSync(join(wrapper, "package.json"), `${JSON.stringify(pkg, null, 2)}\n`)
console.log(`built entroclaw@${version} with platform packages: ${built.join(", ") || "(none)"}`)
