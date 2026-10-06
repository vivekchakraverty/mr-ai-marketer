import React from 'react'
import { createRoot } from 'react-dom/client'
import NavBar from '../../src/renderer/src/components/NavBar'
import BuyerPersonas2 from '../../src/renderer/src/components/BuyerPersonas2'
import { useAppStore } from '../../src/renderer/src/state/store'
import { notifyPersonaJob, useNotifications } from '../../src/renderer/src/state/notifications'
import '../../src/renderer/src/styles/tokens.css'

const testWindow = window as typeof window & {
  runNotificationChecks: () => Promise<string[]>
}
const calls: { path: string; body: unknown }[] = []
let finishTest = false
let failStatusRead = false
window.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const path = new URL(String(input)).pathname
  const body = init?.body ? JSON.parse(String(init.body)) : null
  calls.push({ path, body })
  let result: unknown
  if (path.endsWith('/sources')) result = { sources: [{ key: 'music:demo', label: 'Demo song.mp3', kind: 'Align · Music', clusters: 2 }] }
  else if (path.endsWith('/context')) result = { source_key: 'music:demo', project: { name: 'Demo song', genre: 'Pop' },
    project_origin: { name: 'project', genre: 'project' }, project_details: [], source_updated_at: '',
    clusters: [{ id: 'demo:0', label: 'Melody-led listeners' }, { id: 'demo:1', label: 'Guitar-texture listeners' }],
    audience_signals: [{ id: 'signal:0', claim: 'Artist-stated style' }], platforms: [], affinities: [] }
  else if (path.endsWith('/latest')) result = { job: { id: 'failed-demo', status: 'error', error: 'The profile section needs a complete audience signal citation.', report: null }, previous_report: null }
  else if (path.endsWith('/model-check')) result = { id: 'synthetic-check', status: 'queued', result: {}, error: '' }
  else if (path.endsWith('/model-check/synthetic-check')) {
    if (failStatusRead) { failStatusRead = false; throw new Error('Synthetic status-read interruption') }
    result = { id: 'synthetic-check', status: finishTest ? 'complete' : 'generating', error: '',
    result: finishTest ? { persona_count: 2, models: ['Synthetic test provider'], message: 'Two synthetic music personas passed. No saved song data was used.' } : {} }
  }
  else throw new Error('Unexpected network request in isolated UI verification: ' + path)
  return new Response(JSON.stringify(result), { headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

useAppStore.getState().setHfStatus(true, 'synthetic-ui-test')
useAppStore.getState().goResearch()
function TestApp(): React.JSX.Element {
  const route = useAppStore((state) => state.route)
  return <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}><NavBar />
    <main style={{ padding: 24, overflowY: 'auto', flex: 1 }}><div style={{ maxWidth: 1134, margin: 'auto' }}>
      {route === 'research' ? <BuyerPersonas2 /> : <h1>Home</h1>}
    </div></main></div>
}
createRoot(document.getElementById('root')!).render(<TestApp />)

function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message) }
async function until(check: () => unknown): Promise<void> {
  const deadline = Date.now() + 7000
  while (!check()) {
    if (Date.now() > deadline) throw new Error('UI check timed out')
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
}
function button(text: string): HTMLButtonElement {
  const found = Array.from(document.querySelectorAll('button')).find((item) => item.textContent === text)
  assert(found, 'Missing button: ' + text)
  return found
}
function bell(): HTMLButtonElement { return document.querySelector('.notification-bell')! }
const modelPosts = (): number => calls.filter((call) => call.path.endsWith('/model-check')).length

testWindow.runNotificationChecks = async () => {
  const checks: string[] = []
  await until(() => useNotifications.getState().items.length >= 2)
  assert(modelPosts() === 0, 'Opening the screen must not send a model request')
  notifyPersonaJob({ id: 'failed-demo', status: 'error', error: 'The profile section needs a complete audience signal citation.' })
  assert(useNotifications.getState().items.length === 2, 'Repeated polling must not duplicate notifications')
  bell().click()
  await until(() => document.querySelector('#app-notifications'))
  await until(() => document.querySelector('.notification-panel')!.getBoundingClientRect().top > 0)
  const rect = document.querySelector('.notification-panel')!.getBoundingClientRect()
  assert(rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0, 'Panel must stay in the viewport')
  assert(bell().getAttribute('aria-expanded') === 'true', 'Bell must announce its expanded state')
  checks.push('Bell, badge, deduplication and viewport bounds')
  button('Decline').click()
  await until(() => useNotifications.getState().items.some((item) => item.status === 'declined'))
  assert(modelPosts() === 0, 'Declining must never submit a diagnostic')
  const saved = JSON.parse(localStorage.getItem('mr-ai-marketer-notifications')!)
  assert(saved.state.items.some((item: { status: string }) => item.status === 'declined'), 'Decision must persist')
  checks.push('Decline makes no network request; decision persists')
  notifyPersonaJob({ id: 'another-failure', status: 'error', error: 'A separate synthetic failure' })
  await until(() => Array.from(document.querySelectorAll('button')).some((item) => item.textContent === 'Allow and run test'))
  button('Allow and run test').click()
  await until(() => useNotifications.getState().items.some((item) => item.status === 'running'))
  assert(modelPosts() === 1, 'Allow must submit exactly one diagnostic')
  assert(JSON.stringify(calls.find((call) => call.path.endsWith('/model-check'))!.body) === '{"consent":true}', 'Consent must be explicit; no source or project payload')
  assert(!Array.from(document.querySelectorAll('button')).some((item) => item.textContent === 'Allow and run test'), 'A running request cannot be approved twice')
  checks.push('Allow submits one consent request and prevents double submission')
  failStatusRead = true
  await until(() => useNotifications.getState().items.some((item) => item.status === 'failed' && item.jobId))
  button('Check test status').click()
  await until(() => useNotifications.getState().items.some((item) => item.status === 'running'))
  assert(modelPosts() === 1, 'A status retry must not submit a second generation')
  checks.push('Interrupted status reads resume without another generation')
  for (let index = 0; index < 55; index++) useNotifications.getState().add({
    id: `history-${index}`, kind: 'info', title: 'Synthetic history', message: 'History retention check'
  })
  assert(useNotifications.getState().items.some((item) => item.status === 'running' && item.jobId === 'synthetic-check'),
    'History trimming must retain running tests even after their kind changes from permission to info')
  checks.push('Active tests survive notification history trimming')
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  await until(() => !document.querySelector('#app-notifications'))
  assert(document.activeElement === bell(), 'Escape must return focus to the bell')
  useAppStore.getState().goHome()
  finishTest = true
  await until(() => useNotifications.getState().items.some((item) => item.status === 'complete'))
  bell().click()
  await until(() => document.querySelector('#app-notifications')?.textContent?.includes('Hugging Face model test passed'))
  checks.push('Polling survives panel close and route changes; completion is visible')
  document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
  await until(() => !document.querySelector('#app-notifications'))
  checks.push('Outside click and Escape close the panel')
  return checks
}
