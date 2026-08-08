import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { startBackend, stopBackend, waitForBackendHealth, API_TOKEN, BACKEND_URL } from './backend'
import { getHfToken, getSettings, setHfToken, setSettings, type SettingsPatch } from './settingsStore'
import { bootstrap, detectStatus, RebootRequiredError } from './dockerRuntime'
import { isActivepiecesRunning, startActivepieces, stopActivepieces } from './activepieces'
import { isLeadgenRunning, startLeadgen, stopLeadgen } from './leadgen'
import { checkForUpdate, downloadUpdate, getUpdateState, installUpdate, startUpdateWatch } from './updater'

let mainWindow: BrowserWindow | null = null

/**
 * The host the Mastodon embed in Engage is allowed to be.
 *
 * Read from settings at call time rather than captured once: the user can change
 * instance from the Post Creator without restarting the app, and a stale
 * allow-host would either pin the embed to the old server or block the new one.
 */
function embeddedInstanceHost(): string {
  const raw = (getSettings().mastodonInstance ?? '').trim()
  if (!raw) return ''
  try {
    return new URL(raw.includes('//') ? raw : `https://${raw}`).hostname.toLowerCase()
  } catch {
    return ''
  }
}

/**
 * Hosts the Community section embeds. Telegram Web sends frame-ancestors 'none' like
 * Mastodon does, so it is a <webview> for the same reason — and allow-listed here by exact
 * hostname rather than a suffix match, so a lookalike domain cannot slip through.
 */
const TELEGRAM_WEB_HOSTS = new Set(['web.telegram.org'])

function isEmbeddableUrl(url: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (parsed.protocol !== 'https:') return false

  const hostname = parsed.hostname.toLowerCase()
  if (TELEGRAM_WEB_HOSTS.has(hostname)) return true

  const host = embeddedInstanceHost()
  return Boolean(host) && hostname === host
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 1040,
    minHeight: 680,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#F1EEE2',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      // Engage embeds the user's own Mastodon instance. A Mastodon server sends
      // frame-ancestors 'none', so an iframe cannot show it — a <webview> loads it
      // as its own top-level document, which is the only way to have the real
      // client in the app. Locked down in will-attach-webview just below.
      webviewTag: true,
      // The theme tune is meant to start with the app. Chromium otherwise refuses any audio
      // until the user has clicked something. Scoped to this window: an attached <webview>
      // gets its own webPreferences, so an embedded page still can't autoplay at you.
      autoplayPolicy: 'no-user-gesture-required',
      additionalArguments: [
        `--backend-url=${BACKEND_URL}`,
        // The renderer needs the token to call the backend at all. argv rather than an IPC
        // round trip because the API client is used during module init, before any handler
        // could answer — and argv is already how the backend URL gets there.
        `--api-token=${API_TOKEN}`,
        ...(process.env.DEBUG_ROUTE ? [`--debug-route=${process.env.DEBUG_ROUTE}`] : [])
      ]
    }
  })

  // Whatever the renderer asks for, an attached webview gets no preload, no node,
  // and no src other than the instance the user configured. The renderer is ours,
  // but a webview renders someone else's HTML and this is the seam where that
  // stops being able to matter.
  mainWindow.webContents.on('will-attach-webview', (_event, webPreferences, params) => {
    delete webPreferences.preload
    webPreferences.nodeIntegration = false
    webPreferences.contextIsolation = true
    webPreferences.webSecurity = true
    if (!isEmbeddableUrl(String(params.src ?? ''))) params.src = 'about:blank'
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
    if (process.env.SCREENSHOT_PATH) {
      setTimeout(
        async () => {
          // capturePage only sees the viewport, so a long screen needs scrolling to before
          // the shot to check anything below the fold.
          const scroll = Number(process.env.SCREENSHOT_SCROLL)
          if (scroll) {
            // The page itself doesn't scroll — the header is fixed and the routes live in an
            // overflowing panel — so find whatever element is actually scrollable.
            await mainWindow!.webContents.executeJavaScript(`
              (() => {
                window.scrollTo(0, ${scroll})
                const el = Array.from(document.querySelectorAll('*')).find(
                  (e) => e.scrollHeight > e.clientHeight + 40 && /auto|scroll/.test(getComputedStyle(e).overflowY)
                )
                if (el) el.scrollTop = ${scroll}
              })()
            `)
            await new Promise((resolve) => setTimeout(resolve, 400))
          }
          const image = await mainWindow!.webContents.capturePage()
          const fs = await import('fs')
          fs.writeFileSync(process.env.SCREENSHOT_PATH!, image.toPNG())
          console.log(`[screenshot] saved to ${process.env.SCREENSHOT_PATH}`)
          app.quit()
        },
        Number(process.env.SCREENSHOT_DELAY_MS) || 2500
      )
    }
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    await mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    await mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(async () => {
  electronApp.setAppUserModelId('com.vivekchakraverty.mraimarketer')

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // Keep the Mastodon embed pinned to the instance it belongs to. A link to
  // someone's blog, or to a profile on another server, opens in the real browser
  // instead of quietly turning the embed into a general-purpose browser inside the
  // app. will-redirect is deliberately left alone so a server-side login redirect
  // still works; a login flow that hands off to a different host mid-click will
  // land in the system browser, which is the safe direction to fail in.
  app.on('web-contents-created', (_event, contents) => {
    if (contents.getType() !== 'webview') return
    contents.setWindowOpenHandler(({ url }) => {
      void shell.openExternal(url)
      return { action: 'deny' }
    })
    contents.on('will-navigate', (event, url) => {
      if (isEmbeddableUrl(url)) return
      event.preventDefault()
      void shell.openExternal(url)
    })
  })

  ipcMain.handle('settings:get-hf-token', () => getHfToken())
  ipcMain.handle('settings:set-hf-token', (_event, token: string | null) => {
    setHfToken(token)
  })
  ipcMain.handle('settings:get-all', () => getSettings())
  ipcMain.handle('settings:set-all', (_event, partial: SettingsPatch) => setSettings(partial))
  ipcMain.handle('shell:open-file', (_event, path: string) => shell.openPath(path))
  ipcMain.handle('shell:open-external', (_event, url: string) => shell.openExternal(url))
  ipcMain.handle('update:check', () => checkForUpdate())
  ipcMain.handle('update:download', () => downloadUpdate())
  ipcMain.handle('update:get-state', () => getUpdateState())
  // Returns nothing on purpose: the app is quitting, so there is no renderer left to
  // receive a result.
  ipcMain.handle('update:install', () => installUpdate())

  ipcMain.handle('distribution:detect-status', async () => {
    const dockerStatus = await detectStatus()
    const activepiecesRunning = dockerStatus.dockerRunning ? await isActivepiecesRunning() : false
    return { ...dockerStatus, activepiecesRunning }
  })
  ipcMain.handle('distribution:bootstrap', async (event) => {
    const sendProgress = (step: string): void => event.sender.send('distribution:bootstrap-progress', step)
    try {
      await bootstrap(sendProgress)
      sendProgress('Starting the distribution engine…')
      await startActivepieces()
      return { ok: true as const }
    } catch (err) {
      if (err instanceof RebootRequiredError) {
        return { ok: false as const, rebootRequired: true, message: err.message }
      }
      return { ok: false as const, rebootRequired: false, message: err instanceof Error ? err.message : String(err) }
    }
  })

  ipcMain.handle('leadgen:detect-status', async () => {
    const dockerStatus = await detectStatus()
    const leadgenRunning = dockerStatus.dockerRunning ? await isLeadgenRunning() : false
    return { ...dockerStatus, leadgenRunning }
  })
  ipcMain.handle('leadgen:bootstrap', async (event) => {
    const sendProgress = (step: string): void => event.sender.send('leadgen:bootstrap-progress', step)
    try {
      await bootstrap(sendProgress)
      sendProgress('Starting the lead generation engine…')
      await startLeadgen()
      return { ok: true as const }
    } catch (err) {
      if (err instanceof RebootRequiredError) {
        return { ok: false as const, rebootRequired: true, message: err.message }
      }
      return { ok: false as const, rebootRequired: false, message: err instanceof Error ? err.message : String(err) }
    }
  })

  startBackend()
  try {
    await waitForBackendHealth()
  } catch (err) {
    console.error('Backend failed to become healthy:', err)
  }

  await createWindow()

  // After the window exists, so the first check has somewhere to push its result to.
  startUpdateWatch()

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  stopBackend()
  void stopActivepieces()
  void stopLeadgen()
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  stopBackend()
  void stopActivepieces()
  void stopLeadgen()
})
