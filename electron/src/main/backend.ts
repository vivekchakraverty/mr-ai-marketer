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

/** Brand Studio's bring-your-own-GPU settings, handed over at spawn.
 *
 * modal_backend.py and modal_image_backend.py read BRANDFORGE_MODEL and
 * BRANDFORGE_IMAGE_BUCKET from the environment at import time, and nothing in a packaged
 * install could ever write it — so the BYO-Modal path could not deploy and generation fell
 * back to the hosted Space, which bills its publisher rather than the user. These two are
 * what make "runs on your own GPU, billed to you" actually true.
 *
 * Only set when present, so an operator exporting them before launch still wins. */
function brandForgeEnv(): Record<string, string> {
  const { modelRepo, imageBucket, spaceId } = getSettings().brandForge
  const env: Record<string, string> = {}
  if (modelRepo.trim()) env.BRANDFORGE_MODEL = modelRepo.trim()
  if (imageBucket.trim()) env.BRANDFORGE_IMAGE_BUCKET = imageBucket.trim()
  if (spaceId.trim()) env.BRANDFORGE_SPACE = spaceId.trim()
  return env
}

/** The Space that generates the marketing plan, handed over at spawn.
 *
 * app/config.py reads MARKETING_PLAN_SPACE from the environment, and nothing in a packaged
 * install could ever write it — so the tool would have kept building plans locally no
 * matter what the user set. Only set when present, so an operator exporting the variable
 * before launch still wins. */
function marketingPlanEnv(): Record<string, string> {
  const { spaceUrl } = getSettings().marketingPlan
  return spaceUrl.trim() ? { MARKETING_PLAN_SPACE: spaceUrl.trim() } : {}
}

/** The Spaces the Blog Writer and Email Writer generate on, handed over at spawn.
 *
 * config.py has no defaults for these on purpose — hardcoded ids sent a cloner's traffic to
 * the original author's account — but a packaged install had no way to supply one either,
 * so both tools refused every request and told the user to edit `backend/.env`, which does
 * not exist in an installed build. Only set when present, so an operator exporting the
 * variable before launch still wins. */
/** Email Writer generation on the user's own Modal GPU, handed over at spawn.
 *
 * Falls back to the Brand Studio credentials when its own are blank: a person has one Modal
 * account, and asking for the same token twice to reach the same workspace is worse than a
 * default that just works. Blank on both sides means the free Space is used, which is the
 * normal state and not a degraded one. */
function emailWriterModalEnv(): Record<string, string> {
  const own = getSettings().emailWriterModal
  const shared = getSettings().brandForge
  const tokenId = own.modalTokenId.trim() || shared.modalTokenId.trim()
  const tokenSecret = own.modalTokenSecret.trim() || shared.modalTokenSecret.trim()
  if (!tokenId || !tokenSecret) return {}
  return {
    EMAIL_WRITER_MODAL_TOKEN_ID: tokenId,
    EMAIL_WRITER_MODAL_TOKEN_SECRET: tokenSecret
  }
}

function writerSpaceEnv(): Record<string, string> {
  const { blogWriter, emailWriter } = getSettings().writerSpaces
  const env: Record<string, string> = {}
  if (blogWriter.trim()) env.BLOG_WRITER_SPACE = blogWriter.trim()
  if (emailWriter.trim()) env.EMAIL_WRITER_SPACE = emailWriter.trim()
  return env
}

/** The user's own poster Space, handed over at spawn.
 *
 * Every value here names a repo in THEIR Hugging Face account — the Space is built from
 * resources/poster-space rather than copied from one of ours, so there is no publisher-owned
 * id to configure and nothing of ours in the chain.
 *
 * Only set when present, so an operator exporting a variable before launch still wins. */
function cloudPostingEnv(): Record<string, string> {
  const c = getSettings().cloudPosting
  const env: Record<string, string> = {}
  if (c.spaceId.trim()) env.CLOUD_POSTER_SPACE = c.spaceId.trim()
  if (c.spaceUrl.trim()) env.CLOUD_POSTER_URL = c.spaceUrl.trim()
  if (c.posterKey.trim()) env.CLOUD_POSTER_KEY = c.posterKey.trim()
  if (c.outboxRepo.trim()) env.CLOUD_POSTER_OUTBOX = c.outboxRepo.trim()
  if (c.spaceToken.trim()) env.CLOUD_POSTER_TOKEN = c.spaceToken.trim()
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
      ...brandForgeEnv(),
      ...marketingPlanEnv(),
      ...writerSpaceEnv(),
      ...emailWriterModalEnv(),
      ...hfAssetEnv(),
      ...cloudPostingEnv()
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
      ...brandForgeEnv(),
      ...marketingPlanEnv(),
      ...writerSpaceEnv(),
      ...emailWriterModalEnv(),
      ...hfAssetEnv(),
      ...cloudPostingEnv()
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
