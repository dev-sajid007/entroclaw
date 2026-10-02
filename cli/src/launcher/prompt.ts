// Minimal terminal prompts for the non-TUI subcommands (auth, first-run setup).

import { createInterface } from "node:readline/promises"

export async function ask(question: string, fallback = ""): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  try {
    const answer = (await rl.question(question)).trim()
    return answer || fallback
  } finally {
    rl.close()
  }
}

export async function confirm(question: string, defaultYes = true): Promise<boolean> {
  if (!process.stdin.isTTY) return false
  const answer = (await ask(`${question} ${defaultYes ? "[Y/n]" : "[y/N]"} `)).toLowerCase()
  return answer ? answer.startsWith("y") : defaultYes
}

/** Read a line without echoing it (API keys). Falls back to a normal prompt when stdin isn't a terminal. */
export async function askSecret(question: string): Promise<string> {
  const stdin = process.stdin
  if (!stdin.isTTY) return ask(question)
  process.stdout.write(question)
  // Listen with events rather than `for await`: leaving an async iterator destroys stdin, which the TUI needs next.
  return new Promise((resolve, reject) => {
    let value = ""
    const finish = (error?: Error) => {
      stdin.off("data", onData)
      stdin.setRawMode(false)
      stdin.pause()
      process.stdout.write("\n")
      if (error) reject(error)
      else resolve(value.trim())
    }
    const onData = (chunk: Buffer) => {
      for (const char of chunk.toString("utf8")) {
        if (char === "\r" || char === "\n") return finish()
        if (char === "\u0003") return finish(new Error("cancelled"))
        if (char === "\u007f" || char === "\b") {
          if (value) {
            value = value.slice(0, -1)
            process.stdout.write("\b \b")
          }
        } else if (char >= " ") {
          value += char
          process.stdout.write("*")
        }
      }
    }
    stdin.setRawMode(true)
    stdin.resume()
    stdin.on("data", onData)
  })
}
