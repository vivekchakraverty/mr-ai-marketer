import { useEffect, useRef, useState } from 'react'
import { useAppStore } from '../state/store'
import { primaryButton, secondaryButtonSmall } from '../styles/styleKit'
import type { DockerRuntimeStatus } from '../../../main/dockerRuntime'

type LeadgenStatus = DockerRuntimeStatus & { leadgenRunning: boolean }
type Phase = 'checking' | 'not-ready' | 'setting-up' | 'reboot-required' | 'error' | 'ready'

/** One-click setup for the Lead Gen Agent's self-hosted services (Reacher + SearXNG). Reuses
 * the exact same WSL2/Docker bootstrap the Distribution engine already uses — near-identical
 * to DistributionGateModal, just pointed at the leadgen compose stack. */
export default function LeadgenGateModal(): React.JSX.Element {
  const closeLeadgenGate = useAppStore((s) => s.closeLeadgenGate)
  const setLeadgenEngineReady = useAppStore((s) => s.setLeadgenEngineReady)

  const [phase, setPhase] = useState<Phase>('checking')
  const [status, setStatus] = useState<LeadgenStatus | null>(null)
  const [progressLog, setProgressLog] = useState<string[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const unsubscribeRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    checkStatus()
    return () => unsubscribeRef.current?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function checkStatus(): Promise<void> {
    setPhase('checking')
    const result = await window.api.leadgen.detectStatus()
    setStatus(result)
    if (result.dockerInstalled && result.dockerRunning && result.leadgenRunning) {
      setPhase('ready')
      setLeadgenEngineReady(true)
    } else if (result.dockerInstalled && result.dockerRunning) {
      await handleSetup()
    } else {
      setPhase('not-ready')
    }
  }

  async function handleSetup(): Promise<void> {
    setPhase('setting-up')
    setProgressLog([])
    unsubscribeRef.current = window.api.leadgen.onBootstrapProgress((step) => {
      setProgressLog((log) => [...log, step])
    })
    try {
      const result = await window.api.leadgen.bootstrap()
      unsubscribeRef.current?.()
      if (result.ok) {
        setPhase('ready')
        setLeadgenEngineReady(true)
      } else if (result.rebootRequired) {
        setPhase('reboot-required')
      } else {
        setPhase('error')
        setErrorMessage(result.message ?? 'Setup failed.')
      }
    } catch (err) {
      unsubscribeRef.current?.()
      setPhase('error')
      setErrorMessage(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(43, 36, 32, 0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 50
      }}
    >
      <div
        style={{
          width: 480,
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 22,
          padding: 34,
          boxShadow: '9px 10px 0 rgba(43,36,32,.22)'
        }}
      >
        <div style={{ font: "700 11px 'Quicksand'", letterSpacing: '.16em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
          Lead Gen Agent
        </div>
        <div style={{ font: "700 26px 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>Set up the lead engine</div>

        {phase === 'checking' && (
          <p style={{ font: "600 14px/1.6 'Quicksand'", color: 'var(--ink-body)', marginTop: 10 }}>Checking your system…</p>
        )}

        {phase === 'not-ready' && (
          <>
            <p style={{ font: "600 14px/1.6 'Quicksand'", color: 'var(--ink-body)', marginTop: 10 }}>
              The Lead Gen Agent finds and verifies leads using two free, self-hosted tools —
              a search engine (SearXNG) and an email verifier (Reacher) — running entirely on
              your machine. This is a one-time setup: we install a lightweight Linux environment
              (WSL2) and Docker Engine — no separate Docker Desktop app needed.
            </p>
            {status && !status.wslInstalled && (
              <p style={{ font: "700 12.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 10 }}>
                This may require a one-time restart of your computer partway through.
              </p>
            )}
          </>
        )}

        {phase === 'setting-up' && (
          <div style={{ marginTop: 14 }}>
            {progressLog.length === 0 && (
              <div style={{ font: "700 13px 'Quicksand'", color: 'var(--ink-muted)' }}>Starting setup…</div>
            )}
            {progressLog.map((step, i) => (
              <div
                key={i}
                style={{
                  font: "700 13px 'Quicksand'",
                  color: i === progressLog.length - 1 ? 'var(--ink)' : 'var(--ink-faint)',
                  marginTop: 6
                }}
              >
                {i === progressLog.length - 1 ? '→ ' : '✓ '}
                {step}
              </div>
            ))}
          </div>
        )}

        {phase === 'reboot-required' && (
          <p style={{ font: "700 14px/1.6 'Quicksand'", color: '#a34a3a', marginTop: 10 }}>
            Restart your computer to finish enabling WSL2, then reopen the Lead Gen Agent to continue setup.
          </p>
        )}

        {phase === 'error' && (
          <p style={{ font: "700 13px/1.6 'Quicksand'", color: '#a34a3a', marginTop: 10 }}>{errorMessage}</p>
        )}

        {phase === 'ready' && (
          <p style={{ font: "600 14px/1.6 'Quicksand'", color: 'var(--ink-body)', marginTop: 10 }}>
            The lead engine is up and running.
          </p>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
          {(phase === 'not-ready' || phase === 'error') && (
            <div style={primaryButton} onClick={handleSetup}>
              Set up the lead engine
            </div>
          )}
          {phase === 'ready' && (
            <div style={primaryButton} onClick={closeLeadgenGate}>
              Continue
            </div>
          )}
          {phase !== 'setting-up' && (
            <div style={secondaryButtonSmall} onClick={closeLeadgenGate}>
              Not now
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
