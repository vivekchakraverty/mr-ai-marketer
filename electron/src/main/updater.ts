/**
 * Update checking, downloading and installing, on top of electron-updater.
 *
 * The feed is the project's own GitHub Releases page — electron-builder writes an
 * `app-update.yml` into the packaged app's resources from the `publish` block in
 * package.json, and electron-updater reads the feed URL from there. Change the repo in
 * package.json, not here; there is deliberately no second copy of the owner/repo to drift.
 *
 * Two decisions worth knowing about:
 *
 * 1. `autoDownload = false`. The installer is roughly 600MB. Fetching that in the background
 *    the moment a release appears spends someone's bandwidth without asking, which is a bad
 *    trade on a metered or slow connection. So a check only ever *reports*; the download
 *    starts when the user asks for it. Repeat updates are usually far smaller than the full
 *    installer because electron-updater downloads only the changed blocks, using the
 *    .blockmap file electron-builder publishes next to each installer — but "usually far
 *    smaller" is not a promise you can spend on someone's behalf.
 *
 * 2. `autoInstallOnAppQuit = true`. Once a download has finished, the bytes are already on
 *    disk and the user has already consented to this update. Installing on the next quit
 *    means clicking "Later" doesn't throw the download away.
 *
 * The app is not code-signed, which matters here: electron-updater only verifies the
 * downloaded installer's Authenticode signature when `publisherName` is set in the build
 * config. It deliberately is not set, because setting it on an unsigned build makes every
 * update fail verification. If a signing certificate is ever added, set `publisherName` at
 * the same time — otherwise the update channel stays unauthenticated even after signing.
 */
import { app, BrowserWindow } from 'electron'
import { autoUpdater } from 'electron-updater'
import { stopBackend } from './backend'

export type UpdatePhase =
  | 'idle'
  | 'checking'
  | 'available'
  | 'up-to-date'
  | 'downloading'
  | 'ready'
  | 'error'

export interface UpdateState {
  phase: UpdatePhase
  currentVersion: string
  latestVersion?: string
  /** Release notes from the GitHub release body. May be HTML or markdown. */
  notes?: string
  /** 0–100, only while phase is 'downloading'. */
  percent?: number
  /** Bytes, only while phase is 'downloading'. */
  transferred?: number
  total?: number
  error?: string
  /**
   * True when running from source rather than an installed build. Updating is impossible
   * then, and saying so plainly is better than reporting a failure the developer can't act on.
   */
  devMode?: boolean
}

let state: UpdateState = { phase: 'idle', currentVersion: app.getVersion() }

/** Push state to every open window so the banner and the Settings screen never disagree. */
function setState(patch: Partial<UpdateState>): void {
  state = { ...state, ...patch }
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) window.webContents.send('update:state', state)
  }
}

export function getUpdateState(): UpdateState {
  return state
}

function describe(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err)
  // electron-updater surfaces a missing/unreadable feed as a 404 or a YAML parse failure.
  // Neither phrasing means anything to someone who just wants to know if they're up to date.
  if (/404|Cannot find latest\.yml|HttpError: 404/i.test(message)) {
    return 'No published releases found yet.'
  }
  if (/ENOTFOUND|EAI_AGAIN|ETIMEDOUT|ECONNREFUSED|network/i.test(message)) {
    return "Couldn't reach GitHub to check for updates."
  }
  return message
}

let wired = false

/**
 * Attach the electron-updater event handlers exactly once.
 *
 * These are registered lazily rather than at import time so that a dev run — where the
 * updater can't work at all — never touches electron-updater's machinery.
 */
function wire(): void {
  if (wired) return
  wired = true

  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('update-available', (info) => {
    setState({ phase: 'available', latestVersion: info.version, notes: normaliseNotes(info.releaseNotes), error: undefined })
  })

  autoUpdater.on('update-not-available', () => {
    setState({ phase: 'up-to-date', latestVersion: undefined, error: undefined })
  })

  autoUpdater.on('download-progress', (progress) => {
    setState({
      phase: 'downloading',
      percent: Math.round(progress.percent),
      transferred: progress.transferred,
      total: progress.total
    })
  })

  autoUpdater.on('update-downloaded', (info) => {
    setState({ phase: 'ready', latestVersion: info.version, percent: 100, error: undefined })
  })

  autoUpdater.on('error', (err) => {
    setState({ phase: 'error', error: describe(err) })
  })
}

/** GitHub release bodies arrive as a string, or as an array of per-version blocks. */
function normaliseNotes(notes: string | { version: string; note: string | null }[] | null | undefined): string | undefined {
  if (!notes) return undefined
  if (typeof notes === 'string') return notes
  return notes
    .map((entry) => entry.note ?? '')
    .filter(Boolean)
    .join('\n\n')
}

export async function checkForUpdate(): Promise<UpdateState> {
  if (!app.isPackaged) {
    setState({ phase: 'idle', devMode: true, error: undefined })
    return state
  }
  wire()
  setState({ phase: 'checking', error: undefined })
  try {
    // The 'update-available' / 'update-not-available' handlers above set the real phase;
    // this await is only here so a thrown error becomes an error state rather than an
    // unhandled rejection.
    await autoUpdater.checkForUpdates()
  } catch (err) {
    setState({ phase: 'error', error: describe(err) })
  }
  return state
}

export async function downloadUpdate(): Promise<UpdateState> {
  if (!app.isPackaged) return state
  wire()
  // Guard against a second click while a download is already running: electron-updater
  // would start a parallel download of the same file.
  if (state.phase === 'downloading' || state.phase === 'ready') return state
  setState({ phase: 'downloading', percent: 0, transferred: 0, error: undefined })
  try {
    await autoUpdater.downloadUpdate()
  } catch (err) {
    setState({ phase: 'error', error: describe(err) })
  }
  return state
}

/**
 * Re-check periodically, not just at launch.
 *
 * This app is the kind that gets opened on Monday and left running, so a check that only
 * happens at startup can go weeks without firing. Six hours is frequent enough to notice a
 * release the same day and rare enough to be invisible — a check is one small request to
 * GitHub, and `autoDownload = false` means finding something costs nothing but a banner.
 *
 * Deliberately skipped once a download is under way or finished: re-checking then would
 * either restart a download the user is watching, or replace a "ready to install" state with
 * a fresh "available" one for the same version.
 */
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

export function startUpdateWatch(): void {
  if (!app.isPackaged) return
  const timer = setInterval(() => {
    if (state.phase === 'downloading' || state.phase === 'ready') return
    void checkForUpdate()
  }, CHECK_INTERVAL_MS)
  // Don't hold the event loop open on quit.
  timer.unref?.()
}

/**
 * Quit and run the downloaded installer.
 *
 * The backend is stopped first and deliberately, rather than left to the `before-quit`
 * handler: quitAndInstall launches the NSIS installer and quits in the same breath, and the
 * Python backend holds open file handles inside the install directory. If it is still alive
 * when the installer starts overwriting files, the update half-applies. The installer's own
 * `customInit` taskkill (electron/build/installer.nsh) is the backstop for the case where
 * the process was orphaned by an earlier crash and this call can't see it.
 */
export function installUpdate(): void {
  if (!app.isPackaged || state.phase !== 'ready') return
  stopBackend()
  autoUpdater.quitAndInstall()
}
