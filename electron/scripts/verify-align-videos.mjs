import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'

const source = readFileSync(resolve('src/renderer/src/routes/videoFootprintState.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
const { startVideoPolling, videoAnalysisState, videoSourceState, videoViralityScore } = await import(`data:text/javascript,${encodeURIComponent(compiled)}`)

assert.equal(videoSourceState(null), 'checking')
assert.equal(videoSourceState({ available: false }), 'unavailable')
assert.equal(videoSourceState({ available: true }), 'available')
assert.equal(videoAnalysisState(false, null), 'ready')
assert.equal(videoAnalysisState(true, null), 'uploading')
assert.equal(videoAnalysisState(false, { status: 'comparing', report: null }), 'processing')
assert.equal(videoAnalysisState(false, { status: 'failed', report: null }), 'failed')
assert.equal(videoAnalysisState(false, { status: 'completed', report: { matches: [] } }), 'no_matches')
assert.equal(videoAnalysisState(false, { status: 'completed', report: { matches: [{ platform: 'youtube' }] } }), 'matches')
assert.equal(videoViralityScore({ matches: [], content_virality: { score: 0 } }), null)
assert.equal(videoViralityScore({ matches: [{}], content_virality: { score: null } }), null)
assert.equal(videoViralityScore({ matches: [{}], content_virality: { score: 73 } }), 73)

// A late response from a cancelled job must not replace the next job's report.
let finishOld
const published = []
const stopOld = startVideoPolling({
  read: () => new Promise((resolveJob) => { finishOld = resolveJob }),
  publish: (job) => published.push(job.id),
  onError: (error) => { throw error },
  schedule: () => { throw new Error('Cancelled job must not schedule another poll') }
})
stopOld()
const stopNew = startVideoPolling({
  read: async () => ({ id: 'new', status: 'completed' }),
  publish: (job) => published.push(job.id),
  onError: (error) => { throw error },
  schedule: () => { throw new Error('Completed job must not schedule another poll') }
})
finishOld({ id: 'old', status: 'completed' })
await new Promise((resolveTick) => setImmediate(resolveTick))
assert.deepEqual(published, ['new'])
stopNew()

let scheduled
const stages = [{ status: 'comparing' }, { status: 'completed' }]
const progress = []
const stopProgress = startVideoPolling({
  read: async () => stages.shift(), publish: (job) => progress.push(job.status),
  onError: (error) => { throw error },
  schedule: (callback) => { scheduled = callback; return () => { scheduled = undefined } }
})
await new Promise((resolveTick) => setImmediate(resolveTick))
assert.deepEqual(progress, ['comparing'])
scheduled()
await new Promise((resolveTick) => setImmediate(resolveTick))
assert.deepEqual(progress, ['comparing', 'completed'])
stopProgress()
console.log('Align Videos report states and polling cancellation passed.')
