/**
 * Node shims required by @perawallet/connect (and related crypto deps)
 * when running in the browser.
 */
import { Buffer } from 'buffer'

const g = globalThis as typeof globalThis & {
  Buffer?: typeof Buffer
  global?: typeof globalThis
  process?: { env: Record<string, string | undefined> }
}

g.Buffer ??= Buffer
g.global ??= g
if (!g.process) g.process = { env: {} }
else if (!g.process.env) g.process.env = {}
