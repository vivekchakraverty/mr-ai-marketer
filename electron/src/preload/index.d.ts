import { ElectronAPI } from '@electron-toolkit/preload'
import type { AppSettings, SettingsPatch } from '../main/settingsStore'
import type { DockerRuntimeStatus } from '../main/dockerRuntime'
import type { UpdateState } from '../main/updater'
import type { WorkspaceSnapshot } from '../main/workspaces'

export interface DistributionStatus extends DockerRuntimeStatus {
  activepiecesRunning: boolean
}

export interface LeadgenStatus extends DockerRuntimeStatus {
  leadgenRunning: boolean
}

export interface MrAiMarketerApi {
  backendUrl: string
  apiToken: string
  debugRoute: string | null
  settings: {
    getHfToken: () => Promise<string | null>
    setHfToken: (token: string | null) => Promise<void>
    getAll: () => Promise<AppSettings>
    setAll: (partial: SettingsPatch) => Promise<AppSettings>
  }
  workspaces: {
    list: () => Promise<WorkspaceSnapshot>
    create: (name: string) => Promise<WorkspaceSnapshot>
    rename: (id: string, name: string) => Promise<WorkspaceSnapshot>
    switchTo: (id: string) => Promise<void>
  }
  openFile: (path: string) => Promise<string>
  /** Ask the user where to put these bytes. Resolves false if they cancelled. */
  saveBytes: (suggestedName: string, data: Uint8Array) => Promise<boolean>
  /** Opens a file dialog and copies the chosen file where the backend can read it.
   *  Pass true to offer audio as well as video — only where the network can take it.
   *  Resolves to null if the person cancelled. */
  chooseVideo: (allowAudio?: boolean) => Promise<{ url: string; name: string; bytes: number } | null>
  openExternal: (url: string) => Promise<void>
  update: {
    check: () => Promise<UpdateState>
    download: () => Promise<UpdateState>
    getState: () => Promise<UpdateState>
    install: () => Promise<void>
    onState: (callback: (state: UpdateState) => void) => () => void
  }
  distribution: {
    detectStatus: () => Promise<DistributionStatus>
    bootstrap: () => Promise<{ ok: boolean; rebootRequired?: boolean; message?: string }>
    onBootstrapProgress: (callback: (step: string) => void) => () => void
  }
  leadgen: {
    detectStatus: () => Promise<LeadgenStatus>
    bootstrap: () => Promise<{ ok: boolean; rebootRequired?: boolean; message?: string }>
    onBootstrapProgress: (callback: (step: string) => void) => () => void
  }
}

declare global {
  interface Window {
    electron: ElectronAPI
    api: MrAiMarketerApi
  }
}
