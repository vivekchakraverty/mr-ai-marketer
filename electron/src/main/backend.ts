import { spawn, ChildProcessWithoutNullStreams } from 'child_process'
import { randomBytes } from 'crypto'
import { app } from 'electron'
import { join } from 'path'
import { existsSync } from 'fs'
import { leadgenBackendEnv } from './leadgen'
import { getHfToken, getSettings } from './settingsStore'

/** The one HF token the user configured at the top of Settings, handed to the backend
 * process at spawn so every tool that reads HF_TOKEN from the environment (the Lead Gen
 * Agent, the Social Post Generator) picks it up automatically — no per-tool re-entry, no
 * "Save everything" required. Only set when present, so a saved env file value still loads
 * if the store is empty (python-dotenv won't override a var already in the environment). */
function sharedTokenEnv(): Record<string, string> {
  const token = getHfToken()
  return token ? { HF_TOKEN: token } : {}
}

/** Hugging Face repos holding the datasets and model fetched on first use rather than
 * shipped (backend/app/services/hf_assets.py reads these names from the environment).
 *
 * Without this the packaged app had no way to set them at all: the backend read its own
 * environment, and nothing in an installed build ever wrote to it — so the Influencer
 * Database, Guest Post Suggester and the Email Writer's CTR estimate all failed on any
 * machine that wasn't a dev checkout with the files still on disk. Only set when present,
 * so an operator exporting the variable before launch still wins. */
function hfAssetEnv(): Record<string, string> {
  const { influencerRepo, guestPostRepo, ctrModelRepo } = getSettings().hfAssets
  const env: Record<string, string> = {}
  if (influencerRepo.trim()) env.HF_ASSETS_INFLUENCER_REPO = influencerRepo.trim()
  if (guestPostRepo.trim()) env.HF_ASSETS_GUEST_POST_REPO = guestPostRepo.trim()
  if (ctrModelRepo.trim()) env.HF_ASSETS_CTR_MODEL_REPO = ctrModelRepo.trim()
  return env
}

export const BACKEND_PORT = 8756
export const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`

/**
 * The secret that proves a request came from this app.
 *
 * Binding the backend to 127.0.0.1 does not keep anyone out: any web page the user has open
 * can fetch http://127.0.0.1:8756/… , and the backend has to keep a permissive CORS policy
 * because the packaged renderer runs from file://. So the browser would happily hand another
 * site the response. This token is what a web page cannot obtain.
 *
 * Generated once per launch and never persisted — there is nothing to steal from disk
 * between sessions, and a restart invalidates anything that leaked.
 */
export const API_TOKEN = randomBytes(32).toString('hex')

let backendProcess: ChildProcessWithoutNullStreams | null = null

function resolveDevPython(): string {
  // Prefer the backend's own virtualenv if it's been created; fall back to `python` on PATH.
  const repoRoot = join(__dirname, '..', '..', '..')
  const venvPython =
    process.platform === 'win32'
      ? join(repoRoot, 'backend', '.venv', 'Scripts', 'python.exe')
      : join(repoRoot, 'backend', '.venv', 'bin', 'python')
  return existsSync(venvPython) ? venvPython : 'python'
}

function spawnDevBackend(): ChildProcessWithoutNullStreams {
  const repoRoot = join(__dirname, '..', '..', '..')
  const backendDir = join(repoRoot, 'backend')
  const python = resolveDevPython()
  return spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)], {
    cwd: backendDir,
    env: {
      ...process.env,
      DATA_DIR: app.getPath('userData'),
      MRAIM_API_TOKEN: API_TOKEN,
      ...leadgenBackendEnv(),
      ...sharedTokenEnv(),
      ...hfAssetEnv()
    }
  })
}

function ffmpegDir(): string {
  const platformDir = process.platform === 'win32' ? 'win' : process.platform === 'darwin' ? 'mac' : 'linux'
  // Dev: resources/ffmpeg/<platform>/ next to the repo root. Packaged: bundled as an
  // extraResource at the same relative layout under process.resourcesPath.
  return app.isPackaged
    ? join(process.resourcesPath, 'ffmpeg', platformDir)
    : join(__dirname, '..', '..', '..', 'resources', 'ffmpeg', platformDir)
}

function spawnPackagedBackend(): ChildProcessWithoutNullStreams {
  // PyInstaller onedir build, bundled under process.resourcesPath/backend/mr-ai-marketer-backend(.exe)
  const exeName = process.platform === 'win32' ? 'mr-ai-marketer-backend.exe' : 'mr-ai-marketer-backend'
  const exePath = join(process.resourcesPath, 'backend', exeName)
  // DocuMaker (config.FFMPEG_BIN, default "ffmpeg") and TutorialMaker (frames.py, hardcoded
  // "ffmpeg") both resolve the binary via PATH rather than an absolute path — prepend the
  // bundled ffmpeg/ffprobe directory so neither vendored module needs to be modified.
  const pathSep = process.platform === 'win32' ? ';' : ':'
  return spawn(exePath, ['--port', String(BACKEND_PORT)], {
    env: {
      ...process.env,
      DATA_DIR: app.getPath('userData'),
      MRAIM_API_TOKEN: API_TOKEN,
      PATH: `${ffmpegDir()}${pathSep}${process.env.PATH ?? ''}`,
      ...leadgenBackendEnv(),
      ...sharedTokenEnv(),
      ...hfAssetEnv()
    }
  })
}

export function startBackend(): ChildProcessWithoutNullStreams {
  backendProcess = app.isPackaged ? spawnPackagedBackend() : spawnDevBackend()

  backendProcess.stdout.on('data', (chunk) => {
    console.log(`[backend] ${chunk.toString().trimEnd()}`)
  })
  backendProcess.stderr.on('data', (chunk) => {
    console.error(`[backend] ${chunk.toString().trimEnd()}`)
  })
  backendProcess.on('exit', (code, signal) => {
    console.log(`[backend] exited (code=${code}, signal=${signal})`)
    backendProcess = null
  })

  return backendProcess
}

export function stopBackend(): void {
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill()
    backendProcess = null
  }
}

// Packaged cold starts (first launch after install, disk cache cold, catalog xlsx parse +
// PyInstaller's own unpack-on-first-run overhead) can comfortably exceed 30s — 90s gives
// real headroom without masking a genuinely broken backend forever.
/**
 * Wait for the backend to answer /health.
 *
 * The budget is generous because backend startup imports torch, transformers, chromadb and
 * sentence-transformers before it serves anything — measured at ~70s on a warm dev machine,
 * and slower on a cold or busy one. At the old 90s this failed outright and the whole app
 * refused to launch, which is a far worse outcome than waiting a little longer on a slow
 * boot. The splash screen is showing throughout, so the wait is visible rather than a hang.
 */
export async function waitForBackendHealth(timeoutMs = 240000, intervalMs = 250): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      // /health is deliberately exempt from the token check — it exists to answer this poll
      // before anything else is ready, and it returns nothing but "ok".
      const res = await fetch(`${BACKEND_URL}/health`)
      if (res.ok) return
    } catch {
      // backend not up yet — keep polling
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error(`Backend did not become healthy within ${timeoutMs}ms`)
}
