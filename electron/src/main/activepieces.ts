import { app } from 'electron'
import { randomBytes } from 'crypto'
import { existsSync, mkdirSync, writeFileSync } from 'fs'
import { join } from 'path'
import { dockerCommand, toWslPath, startWslKeepAlive, stopWslKeepAlive } from './dockerRuntime'

export const ACTIVEPIECES_PORT = 8081
// 127.0.0.1, not localhost. Node resolves `localhost` verbatim and hands back ::1 first on
// Windows, while the container publishes on IPv4 loopback — the same mismatch that already
// had to be fixed on the Python side (see backend/app/config.py's ACTIVEPIECES_URL). Naming
// the address leaves nothing for a resolver to decide.
export const ACTIVEPIECES_URL = `http://127.0.0.1:${ACTIVEPIECES_PORT}`

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

const CONTAINER_NAME = 'mr-ai-marketer-activepieces'

/** Matches only the backup names Compose itself generates: `<old-short-id>_<container name>`. */
const RECREATE_BACKUP_PATTERN = `^[0-9a-f]+_${CONTAINER_NAME}$`

/**
 * Clears the leftovers of an interrupted recreate.
 *
 * To replace a container, Compose first renames the running one to `<short-id>_<name>` as a
 * backup, then creates its replacement. Kill the app (or let WSL shut down) mid-recreate and
 * that renamed backup survives — after which every later recreate collides with it and the
 * engine can never start again, which is what "container name is already in use" means here.
 *
 * Only Compose's own renamed backups are removed, never the live container: the anchored
 * pattern cannot match the bare name. Doing so is safe because flows, connections and run
 * history live in the AP_DATA_DIR bind mount rather than inside the container.
 */
async function removeStaleRecreateBackups(): Promise<string[]> {
  const listed = await dockerCommand([
    'ps',
    '-a',
    '--filter',
    `name=${RECREATE_BACKUP_PATTERN}`,
    '--format',
    '{{.Names}}'
  ])
  if (listed.code !== 0) return []

  // Re-check each name here rather than trusting the daemon-side filter, so a future change
  // to Docker's matching can never widen this into removing the live container.
  const stale = listed.stdout
    .split('\n')
    .map((name) => name.trim())
    .filter((name) => new RegExp(RECREATE_BACKUP_PATTERN).test(name))

  if (stale.length === 0) return []
  await dockerCommand(['rm', '-f', ...stale])
  return stale
}

export async function startActivepieces(): Promise<void> {
  startWslKeepAlive() // keep the WSL2 VM from idling itself out from under the container
  ensureSecrets()
  dataDir() // ensure it exists before Docker tries to bind-mount it
  let result = await dockerCommand(composeArgs(['up', '-d']))

  if (result.code !== 0 && result.stderr.includes('is already in use by container')) {
    const removed = await removeStaleRecreateBackups()
    if (removed.length > 0) result = await dockerCommand(composeArgs(['up', '-d']))
  }

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
