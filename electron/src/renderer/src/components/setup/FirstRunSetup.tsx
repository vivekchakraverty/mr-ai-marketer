import { useEffect, useState } from 'react'
import { fetchDistributionChannels, verifyHfToken } from '../../api/client'
import { PLATFORM_SETUP_GUIDES } from '../../state/platformSetupGuides'
import { SETUP_STEPS, stepIndex, type SetupStep } from '../../state/setupSteps'
import { useAppStore } from '../../state/store'
import { label, secondaryButtonSmall, textInputOnSurface } from '../../styles/styleKit'
import ChannelConnectForm from '../ChannelConnectForm'
import CloudCredentialPanel from './CloudCredentialPanel'
import CloudStep from './CloudStep'
import SetupStepShell from './SetupStepShell'

interface ShellHandles {
  stepId: string
  onBack?: () => void
  onFinishLater: () => void
}

/**
 * The first-run credential walkthrough.
 *
 * Mounted as a route takeover rather than an overlay (see MainContent in App.tsx), which
 * keeps the nav bar visible and clickable. That is deliberate: everything here is optional,
 * and a modal that dims the app while asking for eight credentials reads as a wall rather
 * than an offer.
 *
 * It never asks twice. Progress is written to settings.setupWizard on every step change, so
 * closing the app mid-walkthrough resumes where it stopped, and "Finish later" stops it
 * opening on its own again while leaving Settings -> Setup walkthrough to reopen it.
 */
export default function FirstRunSetup(): React.JSX.Element {
  const setupStep = useAppStore((s) => s.setupStep)
  const setSetupStep = useAppStore((s) => s.setSetupStep)
  const closeSetup = useAppStore((s) => s.closeSetup)
  const setHfStatus = useAppStore((s) => s.setHfStatus)

  const [connected, setConnected] = useState<Set<string>>(new Set())
  const [engineReady, setEngineReady] = useState<boolean | null>(null)
  const [skipped, setSkipped] = useState<string[]>([])

  const index = stepIndex(setupStep || SETUP_STEPS[0].id)
  const step: SetupStep = SETUP_STEPS[index]

  // Channel status drives both the per-step "already connected" wording and the summary.
  // Fetched once and refreshed after a connect rather than polled — nothing else changes it
  // while the walkthrough is on screen.
  async function refreshChannels(): Promise<void> {
    try {
      const res = await fetchDistributionChannels()
      setEngineReady(res.ready)
      setConnected(
        new Set([...res.channels, ...res.communityChannels].filter((c) => c.connected).map((c) => c.channel))
      )
    } catch {
      setEngineReady(false)
    }
  }

  useEffect(() => {
    void refreshChannels()
    void (async () => {
      const saved = await window.api.settings.getAll()
      setSkipped(saved.setupWizard.skipped ?? [])
      if (!setupStep) setSetupStep(saved.setupWizard.resumeAt || SETUP_STEPS[0].id)
      if (!saved.setupWizard.startedAt) {
        await window.api.settings.setAll({ setupWizard: { startedAt: new Date().toISOString() } })
      }
    })()
    // Once, on mount. Step changes persist through goTo instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function goTo(next: number): void {
    const clamped = Math.max(0, Math.min(SETUP_STEPS.length - 1, next))
    const id = SETUP_STEPS[clamped].id
    setSetupStep(id)
    void window.api.settings.setAll({ setupWizard: { resumeAt: id } })
  }

  function skipStep(): void {
    const next = skipped.includes(step.id) ? skipped : [...skipped, step.id]
    setSkipped(next)
    void window.api.settings.setAll({ setupWizard: { skipped: next } })
    goTo(index + 1)
  }

  function finish(how: 'completed' | 'skipped'): void {
    const stamp = new Date().toISOString()
    void window.api.settings.setAll({
      setupWizard: how === 'completed' ? { completedAt: stamp, resumeAt: '' } : { skippedAt: stamp }
    })
    closeSetup()
  }

  const shell: ShellHandles = {
    stepId: step.id,
    onBack: index > 0 ? () => goTo(index - 1) : undefined,
    onFinishLater: () => finish('skipped')
  }

  if (step.kind === 'welcome') {
    return (
      <SetupStepShell
        {...shell}
        eyebrow="Setting up"
        title="Let's get you connected"
        blurb="A few accounts to link. Everything here is optional and can be skipped — you can come back from Settings whenever it suits."
        onNext={() => goTo(index + 1)}
        nextLabel="Start"
      >
        <p style={{ font: "600 13.5px/1.75 'Quicksand'", color: 'var(--ink-body)', margin: 0 }}>
          Nothing you enter leaves this machine except to the service it belongs to. Credentials are stored encrypted
          by your operating system, and the app never sends them anywhere else.
        </p>
      </SetupStepShell>
    )
  }

  if (step.kind === 'hf') {
    return <HfStep shell={shell} index={index} goTo={goTo} onSkip={skipStep} setHfStatus={setHfStatus} />
  }

  if (step.kind === 'cloud') {
    return <CloudStep shell={shell} onDone={() => goTo(index + 1)} onSkip={skipStep} />
  }

  if (step.kind === 'channel' && step.channel) {
    const guide = PLATFORM_SETUP_GUIDES[step.channel]
    const isConnected = connected.has(step.channel)
    return (
      <SetupStepShell
        {...shell}
        eyebrow={`Step ${index} of ${SETUP_STEPS.length - 2}`}
        title={guide.label}
        onSkip={skipStep}
      >
        {/* Keyed on the channel so each step gets a FRESH form. Without it React reuses one
            instance across steps, and `values` — seeded once with useState(() => ...) — keeps
            the previous channel's entries: Bluesky's `password` would still be sitting in the
            Email step's `password` field. The dialog never hit this because it unmounts. */}
        <ChannelConnectForm
          key={step.channel}
          channel={step.channel}
          connected={isConnected}
          engineReady={engineReady !== false}
          guide={guide}
          onConnected={() => {
            void refreshChannels()
            goTo(index + 1)
          }}
          onDisconnected={() => void refreshChannels()}
          extra={(values) => <CloudCredentialPanel channel={step.channel as string} values={values} />}
          footer={({ busy, authKind, connect }) => (
            <div style={{ display: 'flex', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
              {authKind !== 'OAUTH2' && (
                <div style={{ ...secondaryButtonSmall, opacity: busy ? 0.6 : 1 }} onClick={busy ? undefined : connect}>
                  {busy ? 'Connecting…' : isConnected ? 'Save changes' : 'Connect'}
                </div>
              )}
              <div style={secondaryButtonSmall} onClick={() => goTo(index + 1)}>
                {isConnected ? 'Continue' : 'Do this later'}
              </div>
            </div>
          )}
        />
      </SetupStepShell>
    )
  }

  const missing = SETUP_STEPS.filter((s) => s.kind === 'channel' && s.channel && !connected.has(s.channel))
  return (
    <SetupStepShell {...shell} eyebrow="Setting up" title="All set" onNext={() => finish('completed')} nextLabel="Done">
      <p style={{ font: "600 13.5px/1.75 'Quicksand'", color: 'var(--ink-body)', margin: '0 0 14px' }}>
        {connected.size > 0
          ? `${connected.size} channel${connected.size === 1 ? '' : 's'} connected.`
          : 'Nothing connected yet — that is fine.'}
      </p>
      {missing.length > 0 && (
        <p style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-muted)', margin: 0 }}>
          Still to do: {missing.map((s) => s.title).join(', ')}. Settings then Setup walkthrough reopens this at any
          time.
        </p>
      )}
    </SetupStepShell>
  )
}

/** The one credential everything else bills to, so it gets its own step rather than a gate. */
function HfStep({
  shell,
  index,
  goTo,
  onSkip,
  setHfStatus
}: {
  shell: ShellHandles
  index: number
  goTo: (n: number) => void
  onSkip: () => void
  setHfStatus: (connected: boolean, username: string | null) => void
}): React.JSX.Element {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void (async () => {
      const saved = await window.api.settings.getAll()
      if (saved.hfToken) setToken(saved.hfToken)
    })()
  }, [])

  async function save(): Promise<void> {
    if (!token.trim()) return
    setBusy(true)
    setError('')
    try {
      const result = await verifyHfToken(token.trim())
      if (!result.valid) {
        setError(result.detail ?? 'That token could not be verified.')
        return
      }
      await window.api.settings.setHfToken(token.trim())
      setHfStatus(true, result.username)
      goTo(index + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SetupStepShell
      {...shell}
      eyebrow="Step 1"
      title="Hugging Face"
      blurb="Every generator in the studio runs on your own Hugging Face account, billed to you and never to us."
      onNext={() => void save()}
      nextLabel="Verify and continue"
      nextBusy={busy}
      onSkip={onSkip}
    >
      <div style={label}>Access token</div>
      <input
        style={textInputOnSurface}
        type="password"
        value={token}
        placeholder="hf_..."
        onChange={(e) => setToken(e.target.value)}
      />
      <div
        style={{ ...secondaryButtonSmall, marginTop: 12 }}
        onClick={() => void window.api.openExternal('https://huggingface.co/settings/tokens')}
      >
        Open this page →
      </div>
      {error && <div style={{ font: "700 12.5px 'Quicksand'", color: '#a34a3a', marginTop: 12 }}>{error}</div>}
    </SetupStepShell>
  )
}
