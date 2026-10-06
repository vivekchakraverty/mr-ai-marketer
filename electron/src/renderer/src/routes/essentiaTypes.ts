export interface SongSection {
  startSeconds: number
  endSeconds: number
  levelDbfs: number
  peakDbfs: number
  spectralCentroidHz: number
}

export interface EssentiaSongResult {
  durationSeconds: number
  sampleRate: number
  essentiaVersion: string
  sections: SongSection[]
  tempoBpm: number | null
  key: string | null
  keyStrength: number | null
  excerptCount: number
  notes: string[]
}

export type EssentiaWorkerReply =
  | { kind: 'progress'; message: string }
  | { kind: 'result'; result: EssentiaSongResult }
  | { kind: 'error'; message: string }
