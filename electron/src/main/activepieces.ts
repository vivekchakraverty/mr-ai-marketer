import { app } from 'electron'
import { randomBytes } from 'crypto'
import { existsSync, mkdirSync, writeFileSync } from 'fs'
import { join } from 'path'
import {
  dockerCommand,
  detectStatus,
  ensureDaemonRunning,
  toWslPath,
  startWslKeepAlive,
  stopWslKeepAlive
} from './dockerRuntime'

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

/** True once the engine has been set up on this machine — the secrets file is written
 *  the first time it is started, and only then. */
export function hasBeenSetUp(): boolean {
  return existsSync(secretsEnvPath())
}

export type AutoStartOutcome = 'started' | 'already-running' | 'not-set-up' | 'no-docker' | 'failed'

/**
 * Bring the engine up at app launch, if it is this machine's to bring up.
 *
 * WHY THIS IS NOT OPTIONAL ANY MORE. The engine only ran while someone was looking at the
 * Distribute screen and had pressed the button. Everything scheduled through it — a post
 * queued for 3:30am — fires against whatever is running at that moment, so a send queued
 * on Tuesday would simply fail on Wednesday because the app had been restarted in between.
 * A scheduler that only works while you are watching is not a scheduler.
 *
 * WHAT IT DELIBERATELY WILL NOT DO. It never installs anything. `bootstrap()` can install
 * WSL and Docker and may demand a reboot; that is a decision with consequences for the
 * whole machine and it stays behind the button on the Distribute screen, where the user
 * asked for it. This only starts what is already there:
 *
 *   - no secrets file  -> the engine has never been set up here. Do nothing.
 *   - Docker absent    -> do nothing. Installing it is not ours to decide.
 *   - daemon stopped   -> start the daemon (cheap, already installed, no prompts).
 *   - already running  -> nothing to do; this is the common case on a warm machine.
 *
 * Failure is reported, never thrown: the app has to open whether or not Docker cooperates,
 * and the Distribute screen already explains an engine that is down.
 */
export async function startActivepiecesIfConfigured(): Promise<AutoStartOutcome> {
  if (!hasBeenSetUp()) return 'not-set-up'

  const status = await detectStatus()
  if (!status.dockerInstalled) return 'no-docker'
  if (!status.dockerRunning) await ensureDaemonRunning()

  if (await isActivepiecesRunning()) {
    // Still take the keep-alive: the container may have been left running by a previous
    // session, and without it the WSL2 VM can idle out from under it.
    startWslKeepAlive()
    return 'already-running'
  }

  await startActivepieces()
  return 'started'
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
