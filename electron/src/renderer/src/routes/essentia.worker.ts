import Essentia from 'essentia.js/dist/essentia.js-core.es.js'
import { EssentiaWASM } from 'essentia.js/dist/essentia-wasm.es.js'
import type { EssentiaSongResult, EssentiaWorkerReply, SongSection } from './essentiaTypes'

const SECTION_COUNT = 8
const EXCERPT_SECONDS = 30

function reply(message: EssentiaWorkerReply): void {
  self.postMessage(message)
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function decibels(amplitude: number): number {
  return Math.round(20 * Math.log10(Math.max(amplitude, 1e-8)) * 10) / 10
}

function withVector<T>(essentia: Essentia, samples: Float32Array, run: (vector: any) => T): T {
  const vector = essentia.arrayToVector(samples)
  try {
    return run(vector)
  } finally {
    vector.delete()
  }
}

function excerptOffsets(length: number, sampleRate: number): number[] {
  const excerptLength = EXCERPT_SECONDS * sampleRate
  if (length <= excerptLength * 3) return [0]
  return [0, Math.floor((length - excerptLength) / 2), length - excerptLength]
}

function analyze(samples: Float32Array, sampleRate: number): EssentiaSongResult {
  const essentia = new Essentia(EssentiaWASM)
  const durationSeconds = samples.length / sampleRate
  const sections: SongSection[] = []

  try {
    for (let index = 0; index < SECTION_COUNT; index++) {
      const start = Math.floor(index * samples.length / SECTION_COUNT)
      const end = Math.floor((index + 1) * samples.length / SECTION_COUNT)
      const section = samples.subarray(start, end)
      const rms = withVector(essentia, section, (vector) => essentia.RMS(vector).rms)
      let peak = 0
      for (let offset = 0; offset < section.length; offset++) {
        peak = Math.max(peak, Math.abs(section[offset]))
      }
      const center = Math.floor((start + end) / 2)
      const centroidStart = Math.max(start, center - Math.floor(sampleRate / 2))
      const centroidEnd = Math.min(end, centroidStart + sampleRate)
      const centroid = withVector(essentia, samples.subarray(centroidStart, centroidEnd),
        (vector) => essentia.SpectralCentroidTime(vector, sampleRate).centroid)
      sections.push({
        startSeconds: Math.round(start / sampleRate * 10) / 10,
        endSeconds: Math.round(end / sampleRate * 10) / 10,
        levelDbfs: decibels(rms),
        peakDbfs: decibels(peak),
        spectralCentroidHz: Math.round(finite(centroid) ?? 0)
      })
      reply({ kind: 'progress', message: `Measuring section ${index + 1} of ${SECTION_COUNT}…` })
    }

    const notes: string[] = []
    const offsets = excerptOffsets(samples.length, sampleRate)
    const bpmValues: number[] = []
    const keys: { name: string; strength: number }[] = []
    let measured = 0
    for (const offset of offsets) {
      const excerpt = samples.subarray(offset, Math.min(samples.length, offset + (offsets.length === 1 ? samples.length : EXCERPT_SECONDS * sampleRate)))
      const level = withVector(essentia, excerpt, (vector) => essentia.RMS(vector).rms)
      if (level < 0.001 || excerpt.length < sampleRate * 8) continue
      measured++
      reply({ kind: 'progress', message: `Estimating tempo and key (${measured} of ${offsets.length})…` })
      withVector(essentia, excerpt, (vector) => {
        try {
          const rhythm = essentia.RhythmExtractor2013(vector)
          const bpm = finite(rhythm.bpm)
          if (bpm !== null && bpm >= 40 && bpm <= 208) bpmValues.push(bpm)
          for (const key of ['ticks', 'estimates', 'bpmIntervals']) rhythm[key]?.delete?.()
        } catch {
          notes.push('Essentia could not estimate a beat in one excerpt.')
        }
        try {
          const tonal = essentia.KeyExtractor(vector)
          const strength = finite(tonal.strength)
          if (tonal.key && tonal.scale && strength !== null) {
            keys.push({ name: `${tonal.key} ${tonal.scale}`, strength })
          }
        } catch {
          notes.push('Essentia could not estimate a key in one excerpt.')
        }
      })
    }

    bpmValues.sort((a, b) => a - b)
    const tempoBpm = bpmValues.length ? Math.round(bpmValues[Math.floor(bpmValues.length / 2)]) : null
    let key: string | null = null
    let keyStrength: number | null = null
    if (keys.length === 1) {
      key = keys[0].name
      keyStrength = Math.round(keys[0].strength * 100) / 100
    } else if (keys.length > 1) {
      const agreeing = keys.filter((entry) => keys.filter((other) => other.name === entry.name).length > 1)
      if (agreeing.length) {
        key = agreeing[0].name
        keyStrength = Math.round(agreeing.reduce((sum, entry) => sum + entry.strength, 0) / agreeing.length * 100) / 100
      } else {
        notes.push('Key estimates disagreed between excerpts, so no single key is shown.')
      }
    }
    if (!measured) notes.push('The audio is too quiet or short for a useful tempo or key estimate.')
    if (offsets.length > 1) notes.push('Tempo and key use three 30-second excerpts; section levels cover the complete song.')
    else if (measured) notes.push('Tempo and key use the complete song.')
    notes.push('Tempo can resolve at half or double the perceived beat. Key is an algorithmic estimate, not a transcription.')

    return {
      durationSeconds: Math.round(durationSeconds * 10) / 10,
      sampleRate,
      essentiaVersion: essentia.version,
      sections,
      tempoBpm,
      key,
      keyStrength,
      excerptCount: measured,
      notes
    }
  } finally {
    essentia.shutdown()
  }
}

self.onmessage = (event: MessageEvent<{ samples: Float32Array; sampleRate: number }>) => {
  try {
    reply({ kind: 'result', result: analyze(event.data.samples, event.data.sampleRate) })
  } catch (error) {
    reply({ kind: 'error', message: error instanceof Error ? error.message : String(error) })
  }
}
