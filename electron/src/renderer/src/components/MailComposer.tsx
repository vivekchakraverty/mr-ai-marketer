import { useEffect, useState } from 'react'
import { getMailStatus, saveMailSettings, sendMail, verifyMail, verifyMailImap, type MailStatus } from '../api/client'
import { label, primaryButtonSmall, secondaryButtonSmall, textInput, textarea } from '../styles/styleKit'

type Check = { state: 'idle' | 'checking' | 'ok' | 'error'; detail?: string }

const EMPTY_STATUS: MailStatus = {
  configured: false,
  host: '',
  port: 587,
  username: '',
  password: '',
  fromName: '',
  fromEmail: '',
  useTls: true,
  imapHost: '',
  imapPort: 993
}

export default function MailComposer(): React.JSX.Element {
  const [status, setStatus] = useState<MailStatus>(EMPTY_STATUS)
  const [loaded, setLoaded] = useState(false)
  const [showSettings, setShowSettings] = useState(false)

  const [edits, setEdits] = useState<Partial<MailStatus>>({})
  const [savingSettings, setSavingSettings] = useState(false)
  const [check, setCheck] = useState<Check>({ state: 'idle' })
  const [imapCheck, setImapCheck] = useState<Check>({ state: 'idle' })

  const [to, setTo] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState<{ ok: boolean; detail: string } | null>(null)

  async function refresh(): Promise<void> {
    try {
      const s = await getMailStatus()
      setStatus(s)
      setShowSettings((cur) => cur || !s.configured)
    } finally {
      setLoaded(true)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  function field<K extends keyof MailStatus>(key: K): MailStatus[K] {
    return (edits[key] as MailStatus[K]) ?? status[key]
  }

  async function handleSaveSettings(): Promise<void> {
    setSavingSettings(true)
    setCheck({ state: 'idle' })
    try {
      const saved = await saveMailSettings(edits)
      setStatus(saved)
      setEdits({})
    } finally {
      setSavingSettings(false)
    }
  }

  async function handleTest(): Promise<void> {
    setCheck({ state: 'checking' })
    try {
      const res = await verifyMail()
      setCheck({ state: res.valid ? 'ok' : 'error', detail: res.detail })
    } catch (err) {
      setCheck({ state: 'error', detail: err instanceof Error ? err.message : String(err) })
    }
  }

  async function handleTestImap(): Promise<void> {
    setImapCheck({ state: 'checking' })
    try {
      const res = await verifyMailImap()
      setImapCheck({ state: res.valid ? 'ok' : 'error', detail: res.detail })
    } catch (err) {
      setImapCheck({ state: 'error', detail: err instanceof Error ? err.message : String(err) })
    }
  }

  async function handleSend(): Promise<void> {
    const recipients = to.split(/[,\n]/).map((a) => a.trim()).filter(Boolean)
    if (!recipients.length || !subject.trim()) {
      setSendResult({ ok: false, detail: 'Needs at least one recipient and a subject.' })
      return
    }
    setSending(true)
    setSendResult(null)
    try {
      await sendMail(recipients, subject.trim(), body)
      setSendResult({ ok: true, detail: `Sent to ${recipients.join(', ')} ✓` })
      setTo('')
      setSubject('')
      setBody('')
    } catch (err) {
      setSendResult({ ok: false, detail: err instanceof Error ? err.message : String(err) })
    } finally {
      setSending(false)
    }
  }

  if (!loaded) return <></>

  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: 20,
        padding: 20,
        boxShadow: 'var(--shadow-md)',
        marginBottom: 30
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <div
          style={{
            width: 14,
            height: 14,
            background: 'var(--tool-social)',
            border: '2px solid var(--border)',
            borderRadius: '55% 45% 50% 50%',
            flexShrink: 0
          }}
        />
        <div style={{ font: "700 18px 'Kalam'", color: 'var(--ink)' }}>Send an email</div>
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            marginLeft: 'auto',
            font: "700 11.5px 'Quicksand'",
            color: 'var(--ink-faint)'
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: status.configured ? 'var(--tool-distribute)' : 'var(--ink-fainter)'
            }}
          />
          {status.configured ? `Sends as ${status.fromEmail}` : 'Not set up'}
        </span>
      </div>
      <div style={{ font: "600 12.5px/1.55 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 14 }}>
        A direct, right-now send through your own mailbox — for a one-off note, not a queued
        blast. (Newsletter-style broadcasts still go through the Email channel above.)
      </div>

      {showSettings && (
        <div
          style={{
            background: 'var(--tip-bg)',
            border: '2px solid var(--border)',
            borderRadius: 16,
            padding: 16,
            marginBottom: 16
          }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12, marginBottom: 12 }}>
            <div>
              <label style={label}>SMTP host</label>
              <input
                value={field('host')}
                onChange={(e) => setEdits((s) => ({ ...s, host: e.target.value }))}
                placeholder="smtp.gmail.com"
                style={textInput}
              />
            </div>
            <div>
              <label style={label}>Port</label>
              <input
                type="number"
                value={field('port')}
                onChange={(e) => setEdits((s) => ({ ...s, port: Number(e.target.value) }))}
                style={textInput}
              />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            <div>
              <label style={label}>Username</label>
              <input
                value={field('username')}
                onChange={(e) => setEdits((s) => ({ ...s, username: e.target.value }))}
                placeholder="you@example.com"
                style={textInput}
              />
            </div>
            <div>
              <label style={label}>Password</label>
              <input
                type="password"
                value={edits.password ?? ''}
                onChange={(e) => setEdits((s) => ({ ...s, password: e.target.value }))}
                placeholder={status.password ? 'configured — leave blank to keep' : 'an app password, not your login'}
                style={textInput}
              />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            <div>
              <label style={label}>From name</label>
              <input
                value={field('fromName')}
                onChange={(e) => setEdits((s) => ({ ...s, fromName: e.target.value }))}
                placeholder="Optional"
                style={textInput}
              />
            </div>
            <div>
              <label style={label}>From address</label>
              <input
                value={field('fromEmail')}
                onChange={(e) => setEdits((s) => ({ ...s, fromEmail: e.target.value }))}
                placeholder="you@example.com"
                style={textInput}
              />
            </div>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)', marginBottom: 14, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={field('useTls')}
              onChange={(e) => setEdits((s) => ({ ...s, useTls: e.target.checked }))}
            />
            Use STARTTLS (leave on unless your provider says otherwise)
          </label>

          <div style={{ borderTop: '2px solid var(--border)', paddingTop: 14, marginBottom: 14 }}>
            <div style={{ font: "700 12px 'Quicksand'", color: 'var(--ink)', marginBottom: 2 }}>
              Bounce detection (optional)
            </div>
            <div style={{ font: "600 12px/1.5 'Quicksand'", color: 'var(--ink-faint)', marginBottom: 10 }}>
              Reads this same mailbox over IMAP to catch delivery failures. Reuses the username/password
              above — leave the host blank to turn this off.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12, marginBottom: 12 }}>
              <div>
                <label style={label}>IMAP host</label>
                <input
                  value={field('imapHost')}
                  onChange={(e) => setEdits((s) => ({ ...s, imapHost: e.target.value }))}
                  placeholder="imap.gmail.com"
                  style={textInput}
                />
              </div>
              <div>
                <label style={label}>Port</label>
                <input
                  type="number"
                  value={field('imapPort')}
                  onChange={(e) => setEdits((s) => ({ ...s, imapPort: Number(e.target.value) }))}
                  style={textInput}
                />
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <div
                style={{ ...secondaryButtonSmall, opacity: imapCheck.state === 'checking' ? 0.6 : 1 }}
                onClick={imapCheck.state === 'checking' ? undefined : handleTestImap}
              >
                {imapCheck.state === 'checking' ? 'Testing…' : 'Test IMAP connection'}
              </div>
              {imapCheck.state === 'ok' && (
                <span style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent)' }}>{imapCheck.detail}</span>
              )}
              {imapCheck.state === 'error' && (
                <span style={{ font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)' }}>{imapCheck.detail}</span>
              )}
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <div
              style={{ ...primaryButtonSmall, opacity: savingSettings ? 0.6 : 1 }}
              onClick={savingSettings ? undefined : handleSaveSettings}
            >
              {savingSettings ? 'Saving…' : 'Save settings'}
            </div>
            <div
              style={{ ...secondaryButtonSmall, opacity: check.state === 'checking' ? 0.6 : 1 }}
              onClick={check.state === 'checking' ? undefined : handleTest}
            >
              {check.state === 'checking' ? 'Testing…' : 'Test connection'}
            </div>
            {check.state === 'ok' && (
              <span style={{ font: "700 12.5px 'Quicksand'", color: 'var(--accent)' }}>{check.detail}</span>
            )}
            {check.state === 'error' && (
              <span style={{ font: "700 12.5px 'Quicksand'", color: 'var(--danger-ink)' }}>{check.detail}</span>
            )}
            {status.configured && (
              <span
                style={{ font: "700 12px 'Quicksand'", color: 'var(--ink-faint)', cursor: 'pointer', marginLeft: 'auto' }}
                onClick={() => setShowSettings(false)}
              >
                Hide ×
              </span>
            )}
          </div>
        </div>
      )}

      {!showSettings && (
        <div
          style={{ font: "700 12px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginBottom: 14 }}
          onClick={() => setShowSettings(true)}
        >
          Edit mail settings
        </div>
      )}

      <div style={{ opacity: status.configured ? 1 : 0.5, pointerEvents: status.configured ? 'auto' : 'none' }}>
        <div style={{ marginBottom: 10 }}>
          <label style={label}>To</label>
          <input
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="one@example.com, two@example.com"
            style={textInput}
          />
        </div>
        <div style={{ marginBottom: 10 }}>
          <label style={label}>Subject</label>
          <input value={subject} onChange={(e) => setSubject(e.target.value)} style={textInput} />
        </div>
        <div style={{ marginBottom: 12 }}>
          <label style={label}>Message</label>
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={6} style={textarea} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            style={{ ...primaryButtonSmall, opacity: sending ? 0.6 : 1 }}
            onClick={sending ? undefined : handleSend}
          >
            {sending ? 'Sending…' : 'Send'}
          </div>
          {sendResult && (
            <span
              style={{
                font: "700 12.5px 'Quicksand'",
                color: sendResult.ok ? 'var(--accent)' : 'var(--danger-ink)'
              }}
            >
              {sendResult.detail}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
