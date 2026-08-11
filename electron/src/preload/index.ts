import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import type { AppSettings, SettingsPatch } from '../main/settingsStore'
import type { DockerRuntimeStatus } from '../main/dockerRuntime'
import type { UpdateState } from '../main/updater'

type DistributionStatus = DockerRuntimeStatus & { activepiecesRunning: boolean }
type LeadgenStatus = DockerRuntimeStatus & { leadgenRunning: boolean }

const backendUrlArg = process.argv.find((arg) => arg.startsWith('--backend-url='))
const backendUrl = backendUrlArg ? backendUrlArg.split('=')[1] : 'http://127.0.0.1:8756'

const debugRouteArg = process.argv.find((arg) => arg.startsWith('--debug-route='))
const debugRoute = debugRouteArg ? debugRouteArg.split('=')[1] : null

const apiTokenArg = process.argv.find((arg) => arg.startsWith('--api-token='))
const apiToken = apiTokenArg ? apiTokenArg.split('=')[1] : ''

const api = {
  backendUrl,
  // Proves to the backend that a request came from this app. Exposed to the renderer because
  // the renderer is the thing making the requests; it is not a secret *from* the renderer.
  apiToken,
  debugRoute,
  settings: {
    getHfToken: (): Promise<string | null> => ipcRenderer.invoke('settings:get-hf-token'),
    setHfToken: (token: string | null): Promise<void> => ipcRenderer.invoke('settings:set-hf-token', token),
    getAll: (): Promise<AppSettings> => ipcRenderer.invoke('settings:get-all'),
    setAll: (partial: SettingsPatch): Promise<AppSettings> => ipcRenderer.invoke('settings:set-all', partial)
  },
  openFile: (path: string): Promise<string> => ipcRenderer.invoke('shell:open-file', path),
  /** Ask the user where to put these bytes. Resolves false if they cancelled. */
  saveBytes: (suggestedName: string, data: Uint8Array): Promise<boolean> =>
    ipcRenderer.invoke('dialog:save-bytes', suggestedName, data),
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke('shell:open-external', url),
  update: {
    check: (): Promise<UpdateState> => ipcRenderer.invoke('update:check'),
    download: (): Promise<UpdateState> => ipcRenderer.invoke('update:download'),
    getState: (): Promise<UpdateState> => ipcRenderer.invoke('update:get-state'),
    install: (): Promise<void> => ipcRenderer.invoke('update:install'),
    // Download progress arrives as a stream of pushes from the main process rather than by
    // polling — same shape as the bootstrap-progress channels above.
    onState: (callback: (state: UpdateState) => void): (() => void) => {
      const handler = (_event: unknown, state: UpdateState): void => callback(state)
      ipcRenderer.on('update:state', handler)
      return () => ipcRenderer.removeListener('update:state', handler)
    }
  },
  distribution: {
    detectStatus: (): Promise<DistributionStatus> => ipcRenderer.invoke('distribution:detect-status'),
    bootstrap: (): Promise<{ ok: boolean; rebootRequired?: boolean; message?: string }> =>
      ipcRenderer.invoke('distribution:bootstrap'),
    onBootstrapProgress: (callback: (step: string) => void): (() => void) => {
      const handler = (_event: unknown, step: string): void => callback(step)
      ipcRenderer.on('distribution:bootstrap-progress', handler)
      return () => ipcRenderer.removeListener('distribution:bootstrap-progress', handler)
    }
  },
  leadgen: {
    detectStatus: (): Promise<LeadgenStatus> => ipcRenderer.invoke('leadgen:detect-status'),
    bootstrap: (): Promise<{ ok: boolean; rebootRequired?: boolean; message?: string }> =>
      ipcRenderer.invoke('leadgen:bootstrap'),
    onBootstrapProgress: (callback: (step: string) => void): (() => void) => {
      const handler = (_event: unknown, step: string): void => callback(step)
      ipcRenderer.on('leadgen:bootstrap-progress', handler)
      return () => ipcRenderer.removeListener('leadgen:bootstrap-progress', handler)
    }
  }
}

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
}
