// Prompt history for the input box: Up/Down recall, persisted across runs.

import { mkdirSync } from "node:fs"
import { dirname, join } from "node:path"

import { stateDir } from "../launcher/paths.ts"

export const MAX_HISTORY = 200

export function defaultHistoryPath(): string {
  return join(stateDir(), "cli-history.json")
}

export class PromptHistory {
  private entries: string[] = []
  /** Position while browsing; equal to entries.length when not browsing. */
  private cursor = 0
  private draft = ""

  constructor(private readonly path: string | null = defaultHistoryPath()) {}

  async load(): Promise<void> {
    if (!this.path) return
    try {
      const data = (await Bun.file(this.path).json()) as unknown
      if (Array.isArray(data)) this.entries = data.filter((e): e is string => typeof e === "string").slice(-MAX_HISTORY)
    } catch {
      // Missing or unreadable history is not an error.
    }
    this.cursor = this.entries.length
  }

  get all(): readonly string[] {
    return this.entries
  }

  add(entry: string): void {
    const value = entry.trim()
    if (value && this.entries.at(-1) !== value) {
      this.entries.push(value)
      if (this.entries.length > MAX_HISTORY) this.entries.splice(0, this.entries.length - MAX_HISTORY)
      void this.save()
    }
    this.cursor = this.entries.length
    this.draft = ""
  }

  /** Older entry, or null at the oldest. `current` is kept as a draft so Down can return to it. */
  previous(current: string): string | null {
    if (this.cursor === 0) return null
    if (this.cursor === this.entries.length) this.draft = current
    this.cursor--
    return this.entries[this.cursor] ?? null
  }

  /** Newer entry, the saved draft past the newest, or null when not browsing. */
  next(): string | null {
    if (this.cursor >= this.entries.length) return null
    this.cursor++
    return this.cursor === this.entries.length ? this.draft : (this.entries[this.cursor] ?? null)
  }

  private async save(): Promise<void> {
    if (!this.path) return
    try {
      mkdirSync(dirname(this.path), { recursive: true })
      await Bun.write(this.path, JSON.stringify(this.entries))
    } catch {
      // History is a convenience; never fail the UI over it.
    }
  }
}
