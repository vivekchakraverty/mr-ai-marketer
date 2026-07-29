import { ElectronAPI } from '@electron-toolkit/preload'
import type { AppSettings } from '../main/settingsStore'
import type { DockerRuntimeStatus } from '../main/dockerRuntime'
import type { UpdateCheckResult } from '../main/updateChecker'

export interface DistributionStatus extends DockerRuntimeStatus {
  activepiecesRunning: boolean
}

export interface LeadgenStatus extends DockerRuntimeStatus {
  leadgenRunning: boolean
}

export interface MrAiMarketerApi {
  backendUrl: string
  debugRoute: string | null
  settings: {
    getHfToken: () => Promise<string | null>
    setHfToken: (token: string | null) => Promise<void>
    getAll: () => Promise<AppSettings>
    setAll: (partial: Partial<AppSettings>) => Promise<AppSettings>
  }
  openFile: (path: string) => Promise<string>
  openExternal: (url: string) => Promise<void>
  checkForUpdate: () => Promise<UpdateCheckResult>
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
