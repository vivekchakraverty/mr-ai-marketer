import { app } from 'electron'
import { randomBytes } from 'crypto'
import { existsSync, mkdirSync, writeFileSync } from 'fs'
import { join } from 'path'
import { dockerCommand, toWslPath, startWslKeepAlive, stopWslKeepAlive } from './dockerRuntime'

export const ACTIVEPIECES_PORT = 8081
export const ACTIVEPIECES_URL = `http://localhost:${ACTIVEPIECES_PORT}`

function resourcesDir(): string {
  // Dev: resources/activepieces next to the repo root. Packaged: bundled as an extraResource
  // at the same relative layout under process.resourcesPath (mirrors ffmpegDir() in backend.ts).
  return app.isPackaged
    ? join(process.resourcesPath, 'activepieces')
    : join(__dirname, '..', '..', '..', 'resources', 'activepieces')
}

function stateDir(): string {
  const dir = join(app.getPath('userData'), 'activepieces')
  mkdirSync(dir, { recursive: true })
  return dir
}

function secretsEnvPath(): string {
  return join(stateDir(), '.env')
}

function dataDir(): string {
  const dir = join(stateDir(), 'data')
  mkdirSync(dir, { recursive: true })
  return dir
}

/** Generates AP_ENCRYPTION_KEY/AP_JWT_SECRET once and persists them — Activepieces uses the
 * encryption key to protect stored platform credentials, so it must stay stable across
 * restarts or previously-connected channels become undecryptable. */
function ensureSecrets(): void {
  const envPath = secretsEnvPath()
  if (existsSync(envPath)) return

  const encryptionKey = randomBytes(16).toString('hex') // 32 hex chars, per Activepieces' requirement
  const jwtSecret = randomBytes(32).toString('hex')
  const dataDirWsl = toWslPath(dataDir())
  const content = `AP_ENCRYPTION_KEY=${encryptionKey}\nAP_JWT_SECRET=${jwtSecret}\nAP_DATA_DIR=${dataDirWsl}\n`
  writeFileSync(envPath, content, 'utf-8')
}

function composeArgs(subcommand: string[]): string[] {
  const composeFileWsl = toWslPath(join(resourcesDir(), 'docker-compose.yml'))
  const envFileWsl = toWslPath(secretsEnvPath())
  return ['compose', '--env-file', envFileWsl, '-f', composeFileWsl, ...subcommand]
}

export async function startActivepieces(): Promise<void> {
  startWslKeepAlive() // keep the WSL2 VM from idling itself out from under the container
  ensureSecrets()
  dataDir() // ensure it exists before Docker tries to bind-mount it
  const result = await dockerCommand(composeArgs(['up', '-d']))
  if (result.code !== 0) {
    throw new Error(`Failed to start the distribution engine: ${result.stderr.slice(0, 500)}`)
  }
  await waitForActivepiecesHealth()
}

export async function stopActivepieces(): Promise<void> {
  await dockerCommand(composeArgs(['down']))
  stopWslKeepAlive()
}

export async function waitForActivepiecesHealth(timeoutMs = 90000, intervalMs = 1500): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${ACTIVEPIECES_URL}/api/v1/flags`)
      if (res.ok) return
    } catch {
      // not up yet — keep polling
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error(`The distribution engine did not become healthy within ${timeoutMs}ms.`)
}

export async function isActivepiecesRunning(): Promise<boolean> {
  try {
    const res = await fetch(`${ACTIVEPIECES_URL}/api/v1/flags`)
    if (res.ok) startWslKeepAlive() // e.g. container was left running from a prior app session
    return res.ok
  } catch {
    return false
  }
}
