import assert from 'node:assert/strict'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const bundled = await build({
  entryPoints: [join(root, 'src', 'renderer', 'src', 'routes', 'musicDestinations.ts')],
  bundle: true,
  write: false,
  platform: 'node',
  format: 'esm'
})
const { suggestMusicDestinations } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].contents).toString('base64')}`)

const audio = {
  durationSeconds: 40,
  sampleRate: 44100,
  essentiaVersion: 'smoke test',
  tempoBpm: 112,
  key: 'A minor',
  keyStrength: 0.5,
  excerptCount: 1,
  notes: [],
  sections: [
    { startSeconds: 0, endSeconds: 20, levelDbfs: -30, peakDbfs: -12, spectralCentroidHz: 500 },
    { startSeconds: 20, endSeconds: 40, levelDbfs: -10, peakDbfs: -3, spectralCentroidHz: 1200 }
  ]
}
const context = { style: 'dream pop', referenceArtists: '', location: '', goal: 'feedback', stage: 'draft' }
const draft = suggestMusicDestinations(audio, context)
assert.equal(draft[0].id, 'soundcloud')
assert(draft.some((item) => item.id === 'songwriting'))
assert(!draft.some((item) => item.id === 'spotify_pitch'))
assert(draft.find((item) => item.id === 'musicfeedback').nextSteps.some((step) => step.includes('two substantive critiques')))

const upcoming = suggestMusicDestinations(audio, { ...context, goal: 'playlists', stage: 'unreleased' })
assert.equal(upcoming[0].id, 'spotify_pitch')
assert(upcoming[0].boundary.includes('does not guarantee'))

const released = suggestMusicDestinations(audio, { ...context, stage: 'released', location: 'Kolkata' })
assert(!released.some((item) => item.id === 'spotify_pitch' || item.id === 'songwriting'))
assert(released.some((item) => item.id === 'spotify_followers'))
assert(released.some((item) => item.id === 'spotify_artist_pick'))
assert(released.some((item) => item.id === 'youtube_shorts'))
assert(released.some((item) => item.id === 'bandcamp_community'))
assert(released.some((item) => item.id === 'local_scene'))
assert(released.every((item) => item.sourceUrl.startsWith('https://') && item.visitUrl.startsWith('https://') && item.boundary && item.nextSteps.length))

console.log('Music audience routes passed release-stage, source, and recommendation checks.')
