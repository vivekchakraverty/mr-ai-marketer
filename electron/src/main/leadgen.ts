import { app } from 'electron'
import { randomBytes } from 'crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { dockerCommand, toWslPath, startWslKeepAlive, stopWslKeepAlive } from './dockerRuntime'

export const LEADGEN_REACHER_PORT = 8082
export const LEADGEN_SEARXNG_PORT = 8083
// 127.0.0.1 rather than localhost, for the same reason as activepieces.ts: these containers
// publish on IPv4 loopback and Node prefers ::1 for `localhost` on Windows.
export const LEADGEN_REACHER_URL = `http://127.0.0.1:${LEADGEN_REACHER_PORT}`
export const LEADGEN_SEARXNG_URL = `http://127.0.0.1:${LEADGEN_SEARXNG_PORT}`

function resourcesDir(): string {
  // Dev: resources/leadgen next to the repo root. Packaged: bundled as an extraResource
  // at the same relative layout under process.resourcesPath (mirrors activepieces.ts).
  return app.isPackaged
    ? join(process.resourcesPath, 'leadgen')
    : join(__dirname, '..', '..', '..', 'resources', 'leadgen')
}

function stateDir(): string {
  const dir = join(app.getPath('userData'), 'leadgen')
  mkdirSync(dir, { recursive: true })
  return dir
}

function secretsPath(): string {
  return join(stateDir(), 'secrets.json')
}

function secretsEnvPath(): string {
  return join(stateDir(), '.env')
}

function dataDir(): string {
  const dir = join(stateDir(), 'data')
  mkdirSync(dir, { recursive: true })
  return dir
}

function searxngConfigDir(): string {
  const dir = join(stateDir(), 'searxng')
  mkdirSync(dir, { recursive: true })
  return dir
}

interface Secrets {
  searxngSecret: string
}

/** Generates the SearXNG secret once and persists it — stable across restarts. Cheap and
 * Docker-independent (just a JSON file), so it's safe to call eagerly at app startup,
 * before the user has ever opened the tool or Docker/WSL have been set up. */
export function ensureSecrets(): Secrets {
  const path = secretsPath()
  if (existsSync(path)) {
    return JSON.parse(readFileSync(path, 'utf-8')) as Secrets
  }
  const secrets: Secrets = { searxngSecret: randomBytes(32).toString('hex') }
  writeFileSync(path, JSON.stringify(secrets), 'utf-8')
  return secrets
}

/** Materialize the per-user SearXNG settings.yml from the bundled template, baking in the
 * generated secret. Keeps the shipped resource a static template and the running secret
 * per-user (mirrors activepieces.ts's ensureSecrets). Idempotent. */
function ensureSearxngConfig(): void {
  const { searxngSecret } = ensureSecrets()
  const template = readFileSync(join(resourcesDir(), 'searxng', 'settings.yml'), 'utf-8')
  const rendered = template.replace('__SEARXNG_SECRET__', searxngSecret)
  const target = join(searxngConfigDir(), 'settings.yml')
  // Only rewrite when the content would change, so we don't thrash the file (and the
  // container's view of it) on every launch.
  if (!existsSync(target) || readFileSync(target, 'utf-8') !== rendered) {
    writeFileSync(target, rendered, 'utf-8')
  }
}

/** Env vars the Python backend needs to reach the self-hosted services + find its data
 * dir, computed the same way DATA_DIR already is in backend.ts — merged into the spawned
 * backend process's env, not read from a settings screen. Safe to call before Docker/WSL
 * exist; the values are only used once the leadgen daemon actually tries to connect. */
export function leadgenBackendEnv(): Record<string, string> {
  return {
    LEADGEN_DATA_DIR: dataDir(),
    LEADGEN_REACHER_URL: LEADGEN_REACHER_URL,
    LEADGEN_SEARXNG_URL: LEADGEN_SEARXNG_URL
  }
}

function writeComposeEnvFile(): void {
  // Paths handed to `docker compose` must be WSL mount paths (the daemon runs inside WSL2).
  const content =
    `LEADGEN_DATA_DIR=${toWslPath(dataDir())}\n` +
    `LEADGEN_SEARXNG_CONFIG_DIR=${toWslPath(searxngConfigDir())}\n`
  writeFileSync(secretsEnvPath(), content, 'utf-8')
}

function composeArgs(subcommand: string[]): string[] {
  const composeFileWsl = toWslPath(join(resourcesDir(), 'docker-compose.yml'))
  const envFileWsl = toWslPath(secretsEnvPath())
  return ['compose', '--env-file', envFileWsl, '-f', composeFileWsl, ...subcommand]
}

export async function startLeadgen(): Promise<void> {
  startWslKeepAlive() // keep the WSL2 VM from idling itself out from under the containers
  dataDir() // ensure it exists before Docker tries to bind-mount it
  ensureSearxngConfig()
  writeComposeEnvFile()
  const result = await dockerCommand(composeArgs(['up', '-d']))
  if (result.code !== 0) {
    throw new Error(`Failed to start the lead generation engine: ${result.stderr.slice(0, 500)}`)
  }
  await waitForLeadgenHealth()
}

export async function stopLeadgen(): Promise<void> {
  await dockerCommand(composeArgs(['down']))
  stopWslKeepAlive()
}

async function pingService(url: string): Promise<boolean> {
  try {
    const res = await fetch(url)
    return res.ok || res.status === 404 || res.status === 403
  } catch {
    return false
  }
}

export async function waitForLeadgenHealth(timeoutMs = 90000, intervalMs = 1500): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    // Reacher has no dedicated health route; any HTTP response (even a 404/405) means it's
    // up and accepting connections. SearXNG's root serves its search page once ready.
    const [reacherUp, searxngUp] = await Promise.all([
      pingService(`${LEADGEN_REACHER_URL}/`),
      pingService(`${LEADGEN_SEARXNG_URL}/`)
    ])
    if (reacherUp && searxngUp) return
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error(`The lead generation engine did not become healthy within ${timeoutMs}ms.`)
}

export async function isLeadgenRunning(): Promise<boolean> {
  const [reacherUp, searxngUp] = await Promise.all([
    pingService(`${LEADGEN_REACHER_URL}/`),
    pingService(`${LEADGEN_SEARXNG_URL}/`)
  ])
  const running = reacherUp && searxngUp
  if (running) startWslKeepAlive() // e.g. containers were left running from a prior app session
  return running
}
