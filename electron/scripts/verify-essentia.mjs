import { existsSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const electronRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const assets = join(electronRoot, 'out', 'renderer', 'assets')
const workerName = readdirSync(assets).find((name) => /^essentia\.worker-.*\.js$/.test(name))
const license = join(electronRoot, '..', 'resources', 'essentia', 'LICENSE')

if (!workerName || statSync(join(assets, workerName)).size < 1_000_000) {
  throw new Error('The Essentia worker was not bundled into the renderer.')
}
if (!existsSync(license)) throw new Error('The Essentia AGPL license file is missing.')

let result
globalThis.self = globalThis
globalThis.postMessage = (message) => {
  if (message.kind === 'result') result = message.result
  if (message.kind === 'error') throw new Error(message.message)
}
await import(pathToFileURL(join(assets, workerName)).href)

const sampleRate = 44_100
const samples = Float32Array.from(
  { length: sampleRate * 40 },
  (_, index) => Math.sin(2 * Math.PI * 440 * index / sampleRate) * (index < sampleRate * 30 ? 0.02 : 0.25)
)
self.onmessage({ data: { samples, sampleRate } })

if (!result?.essentiaVersion || result.sections.length !== 8 || result.durationSeconds !== 40 ||
    result.sections[7].levelDbfs < result.sections[0].levelDbfs + 15) {
  throw new Error('The packaged Essentia worker failed its local audio smoke test.')
}
console.log(`Essentia ${result.essentiaVersion} packaged and analyzed all 40 seconds of local audio.`)
