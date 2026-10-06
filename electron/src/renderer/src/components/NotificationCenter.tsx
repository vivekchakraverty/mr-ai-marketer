import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { buyerPersonas2Api } from '../api/buyerPersonas2'
import { useNotifications, type AppNotification } from '../state/notifications'
import './NotificationCenter.css'

export default function NotificationCenter(): React.JSX.Element {
  const items = useNotifications((state) => state.items)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState({ top: 0, left: 0, width: 420, maxHeight: 500 })
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const attention = items.filter((item) => !item.read || item.status === 'pending').length
  const activeChecks = items.filter((item) => item.status === 'running' && item.jobId)
    .map((item) => `${item.id}:${item.jobId}`).join('|')

  useEffect(() => {
    if (!activeChecks) return
    let alive = true
    let polling = false
    async function poll(): Promise<void> {
      if (polling) return
      polling = true
      try {
        const checks = useNotifications.getState().items.filter((item) => item.status === 'running' && item.jobId)
        await Promise.all(checks.map(async (item) => {
          try {
            const check = await buyerPersonas2Api.getModelCheck(item.jobId!)
            if (!alive) return
            if (check.status === 'complete') {
              useNotifications.getState().update(item.id, { kind: 'success', title: 'Hugging Face model test passed',
                status: 'complete', read: false, message: `${check.result.message || 'The synthetic test passed.'}\n${(check.result.models || []).join(', ')}` })
            } else if (check.status === 'error') {
              useNotifications.getState().update(item.id, { kind: 'error', title: 'Hugging Face model test failed',
                status: 'failed', jobId: undefined, read: false, message: check.error })
            }
          } catch (error) {
            if (alive) useNotifications.getState().update(item.id, { kind: 'error', status: 'failed', read: false,
              title: 'Could not check the model test', message: error instanceof Error ? error.message : String(error) })
          }
        }))
      } finally { polling = false }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 1800)
    return () => { alive = false; window.clearInterval(timer) }
  }, [activeChecks])

  useEffect(() => {
    if (!open) return
    function reposition(): void {
      const rect = buttonRef.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.min(420, window.innerWidth - 24)
      const top = Math.min(rect.bottom + 10, window.innerHeight - 180)
      setPosition({ top, width, left: Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12)),
        maxHeight: Math.max(150, window.innerHeight - top - 12) })
    }
    function onOutside(event: PointerEvent): void {
      const target = event.target as Node
      if (!buttonRef.current?.contains(target) && !panelRef.current?.contains(target)) setOpen(false)
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === 'Escape') { setOpen(false); buttonRef.current?.focus() }
    }
    reposition()
    useNotifications.getState().markAllRead()
    closeRef.current?.focus()
    document.addEventListener('pointerdown', onOutside)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', reposition)
    return () => {
      document.removeEventListener('pointerdown', onOutside)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', reposition)
    }
  }, [open])

  async function allow(item: AppNotification): Promise<void> {
    const current = useNotifications.getState().items.find((note) => note.id === item.id)
    if (!current || !['pending', 'failed'].includes(current.status || '')) return
    useNotifications.getState().update(item.id, { status: 'starting', read: true })
    try {
      const job = await buyerPersonas2Api.startModelCheck()
      useNotifications.getState().update(item.id, { status: 'running', kind: 'info', jobId: job.id,
        title: 'Testing Hugging Face persona generation',
        message: 'You allowed this test. Generating two synthetic music personas; no saved song data is being sent. You can leave this panel open or return later.' })
    } catch (error) {
      useNotifications.getState().update(item.id, { status: 'failed', kind: 'error', read: false,
        title: 'Could not start the model test', message: error instanceof Error ? error.message : String(error) })
    }
  }

  return <>
    <button ref={buttonRef} type="button" className="notification-bell" title="Notifications"
      aria-label={`Notifications${attention ? `, ${attention} need attention` : ''}`} aria-expanded={open}
      aria-controls="app-notifications" onClick={() => setOpen((value) => !value)}>
      <svg width="21" height="23" viewBox="0 0 24 26" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M5 11a7 7 0 0 1 14 0v5l2 3H3l2-3v-5Z" /><path d="M9 23a3 3 0 0 0 6 0M12 2V1" />
      </svg>
      {attention > 0 && <span className="notification-badge" aria-hidden="true">{attention > 99 ? '99+' : attention}</span>}
    </button>
    {open && createPortal(<div ref={panelRef} id="app-notifications" role="dialog" aria-label="Notifications"
      className="notification-panel" style={position}>
      <header><h2>Notifications</h2><button ref={closeRef} type="button" className="notification-close" aria-label="Close notifications"
        onClick={() => { setOpen(false); buttonRef.current?.focus() }}>×</button></header>
      <div className="notification-toolbar"><span>{items.filter((item) => item.status === 'pending').length} pending requests</span>
        <button type="button" onClick={() => useNotifications.getState().markAllRead()}>Mark all read</button></div>
      <div className="notification-list" aria-live="polite">
        {!items.length && <p className="notification-empty">You’re all caught up. Requests and app updates will appear here.</p>}
        {items.map((item) => {
          const waiting = item.status === 'pending' || item.status === 'failed' && item.action === 'persona-model-check' && !item.jobId
          const interruptedCheck = item.status === 'failed' && !!item.jobId
          const running = item.status === 'starting' || item.status === 'running'
          return <article className={`notification-item notification-${item.kind}`} key={item.id}>
            <div className="notification-item-head"><strong>{item.title}</strong>{!waiting && !running && !interruptedCheck &&
              <button type="button" className="notification-close" aria-label={`Dismiss ${item.title}`}
                onClick={() => useNotifications.getState().dismiss(item.id)}>×</button>}</div>
            <p>{item.message}</p><time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString()}</time>
            {waiting && item.status === 'failed' && <p>Another test uses your saved Hugging Face token and only synthetic text. No saved song data is sent; inference credits may be used.</p>}
            {waiting && <div className="notification-actions"><button type="button" className="notification-allow"
              onClick={() => void allow(item)}>{item.status === 'failed' ? 'Allow another test' : 'Allow and run test'}</button>
              <button type="button" onClick={() => useNotifications.getState().update(item.id, { status: 'declined', kind: 'info',
                title: 'Model test declined', read: true, message: item.status === 'failed' ?
                  'You declined another model test. No new diagnostic request was sent.' :
                  'You declined the synthetic model test. No diagnostic request was sent.' })}>Decline</button></div>}
            {interruptedCheck && <div className="notification-actions"><button type="button" onClick={() =>
              useNotifications.getState().update(item.id, { status: 'running', kind: 'info', read: true,
                title: 'Checking the model test result', message: 'Checking your previously approved test. This does not start another generation.' })}>Check test status</button></div>}
            {running && <p className="notification-progress" role="status">{item.status === 'starting' ? 'Starting…' : 'Test in progress…'}</p>}
          </article>
        })}
      </div>
    </div>, document.body)}
  </>
}
