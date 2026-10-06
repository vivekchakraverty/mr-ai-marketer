import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { readFile, writeFile, mkdir, rm } from 'node:fs/promises'
import { dirname, join, resolve, sep, extname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

// An isolated headless profile and mocked backend. Never attach to the user's app or browser.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const temporary = join(root, '.verification', 'notifications')
assert(temporary.startsWith(root + sep))
await mkdir(temporary, { recursive: true })
await build({ entryPoints: [join(root, 'scripts/fixtures/notifications.tsx')],
  bundle: true, platform: 'browser', format: 'esm', jsx: 'automatic', outfile: join(temporary, 'app.js'),
  loader: { '.png': 'dataurl', '.woff2': 'file', '.woff': 'file', '.ttf': 'file' },
  plugins: [{ name: 'silent-test-audio', setup(builder) {
    builder.onLoad({ filter: /\.mp3$/ }, () => ({ contents: 'export default ""', loader: 'js' }))
  } }] })
await writeFile(join(temporary, 'index.html'), '<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/app.css"></head><body><div id="root"></div><script>localStorage.setItem("mraim.music.on","off")</script><script type="module" src="/app.js"></script></body></html>')
const server = createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, 'http://localhost').pathname
    const file = resolve(temporary, '.' + (pathname === '/' ? '/index.html' : pathname))
    if (!file.startsWith(temporary + sep)) { response.writeHead(403).end(); return }
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2' }[extname(file)]
    response.setHeader('Content-Type', mime || 'application/octet-stream')
    response.end(await readFile(file))
  } catch { response.writeHead(404).end() }
})
await new Promise((done, fail) => { server.once('error', fail); server.listen(0, '127.0.0.1', done) })
const port = server.address().port
const browserPath = process.env.MRAIM_TEST_BROWSER || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const profile = join(temporary, 'profile')
const browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--remote-debugging-port=0', '--remote-allow-origins=*',
  '--user-data-dir=' + profile, `http://127.0.0.1:${port}`], { windowsHide: true, stdio: 'ignore' })
let socket
let nextId = 0
const pending = new Map()
const delay = (ms) => new Promise((done) => setTimeout(done, ms))
async function command(method, params = {}) {
  const id = ++nextId
  return new Promise((done, fail) => {
    const timer = setTimeout(() => { pending.delete(id); fail(new Error('Browser command timed out: ' + method)) }, 20000)
    pending.set(id, { done, fail, timer })
    socket.send(JSON.stringify({ id, method, params }))
  })
}
async function evaluate(expression) {
  const result = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
  return result.result.value
}
try {
  let debugPort
  const deadline = Date.now() + 12000
  while (!debugPort) {
    try { debugPort = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch {}
    if (Date.now() > deadline) throw new Error('The isolated test browser did not start')
    if (!debugPort) await delay(100)
  }
  const tabs = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json())
  const target = tabs.find((tab) => tab.type === 'page' && tab.url.startsWith(`http://127.0.0.1:${port}`))
  assert(target, 'Missing isolated test tab')
  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((done, fail) => { socket.addEventListener('open', done, { once: true }); socket.addEventListener('error', fail, { once: true }) })
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(String(event.data))
    const request = pending.get(message.id)
    if (!request) return
    clearTimeout(request.timer); pending.delete(message.id)
    if (message.error) request.fail(new Error(JSON.stringify(message.error)))
    else request.done(message.result)
  })
  await command('Page.enable')
  for (const [width, height] of [[1536, 850], [600, 850]]) {
    await command('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false })
    await evaluate('localStorage.clear()')
    await command('Page.reload')
    await evaluate('new Promise(resolve => { const wait = () => window.runNotificationChecks ? resolve(true) : setTimeout(wait, 30); wait() })')
    const checks = await evaluate('window.runNotificationChecks()')
    console.log(`${width}px: ${checks.join('; ')}`)
    if (width === 1536) {
      await evaluate('window.location.reload()')
      await delay(500)
      await evaluate('new Promise(resolve => { const wait = () => document.querySelector(".notification-bell") ? resolve(true) : setTimeout(wait, 30); wait() })')
      // Show a fresh fictional request for visual inspection after exercising both decisions.
      await evaluate('localStorage.clear(); window.location.reload()')
      await delay(500)
      await evaluate('new Promise(resolve => { const wait = () => document.querySelector(".notification-bell") && document.querySelector(".bp2-error") ? resolve(true) : setTimeout(wait, 30); wait() })')
      await evaluate('document.querySelector(".notification-bell").click()')
      await delay(200)
      const screenshot = await command('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(root, '..', 'backend', 'notifications-ui.png'), Buffer.from(screenshot.data, 'base64'))
    }
  }
  console.log('Notifications passed consent, persistence, route-change polling, keyboard, and layout checks. All network responses were mocked.')
  await command('Browser.close')
} catch (error) {
  if (socket?.readyState === WebSocket.OPEN) {
    console.error('Isolated UI state:', await evaluate('document.body.innerText').catch(() => 'unavailable'))
  }
  throw error
} finally {
  socket?.close()
  browser.kill()
  server.close()
  // Only generated files in the verified workspace test directory are removed.
  await delay(500)
  await rm(temporary, { recursive: true, force: true }).catch(() => {})
}
