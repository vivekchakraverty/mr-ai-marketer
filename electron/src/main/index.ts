import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { startBackend, stopBackend, waitForBackendHealth, BACKEND_URL } from './backend'
import { getHfToken, getSettings, setHfToken, setSettings, type AppSettings } from './settingsStore'
import { bootstrap, detectStatus, RebootRequiredError } from './dockerRuntime'
import { isActivepiecesRunning, startActivepieces, stopActivepieces } from './activepieces'
import { isLeadgenRunning, startLeadgen, stopLeadgen } from './leadgen'
import { checkForUpdate } from './updateChecker'

let mainWindow: BrowserWindow | null = null

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
      additionalArguments: [
        `--backend-url=${BACKEND_URL}`,
        ...(process.env.DEBUG_ROUTE ? [`--debug-route=${process.env.DEBUG_ROUTE}`] : [])
      ]
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
    if (process.env.SCREENSHOT_PATH) {
      setTimeout(
        async () => {
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

  ipcMain.handle('settings:get-hf-token', () => getHfToken())
  ipcMain.handle('settings:set-hf-token', (_event, token: string | null) => {
    setHfToken(token)
  })
  ipcMain.handle('settings:get-all', () => getSettings())
  ipcMain.handle('settings:set-all', (_event, partial: Partial<AppSettings>) => setSettings(partial))
  ipcMain.handle('shell:open-file', (_event, path: string) => shell.openPath(path))
  ipcMain.handle('shell:open-external', (_event, url: string) => shell.openExternal(url))
  ipcMain.handle('update:check', () => checkForUpdate())

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
