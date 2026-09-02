import { SETUP_STEPS } from '../../state/setupSteps'
import { primaryButton, secondaryButtonSmall } from '../../styles/styleKit'

interface Props {
  stepId: string
  eyebrow: string
  title: string
  blurb?: string
  children: React.ReactNode
  /** Omitted on steps that supply their own primary action (a channel form's Connect). */
  onNext?: () => void
  nextLabel?: string
  nextBusy?: boolean
  onBack?: () => void
  onSkip?: () => void
  onFinishLater: () => void
}

/**
 * The frame every walkthrough step sits in: where you are, and the ways out.
 *
 * "Finish later" is on every step on purpose. The walkthrough asks for credentials to eight
 * different services and nobody has all of them to hand on first launch; a wizard that had
 * to be completed in one sitting to be escaped would be worse than the README it replaces.
 */
export default function SetupStepShell({
  stepId,
  eyebrow,
  title,
  blurb,
  children,
  onNext,
  nextLabel = 'Next',
  nextBusy = false,
  onBack,
  onSkip,
  onFinishLater
}: Props): React.JSX.Element {
  const index = SETUP_STEPS.findIndex((s) => s.id === stepId)

  return (
    <div style={{ maxWidth: 620, margin: '0 auto', padding: '34px 34px 60px' }}>
      <div style={{ display: 'flex', gap: 5, marginBottom: 22, flexWrap: 'wrap' }}>
        {SETUP_STEPS.map((s, i) => (
          <span
            key={s.id}
            title={s.title}
            style={{
              width: i === index ? 22 : 8,
              height: 8,
              borderRadius: 6,
              background: i <= index ? 'var(--accent)' : 'var(--border-soft)',
              border: '1.5px solid var(--border)'
            }}
          />
        ))}
      </div>

      <div
        style={{
          font: "700 11px 'Quicksand'",
          letterSpacing: '.16em',
          textTransform: 'uppercase',
          color: 'var(--ink-faint)'
        }}
      >
        {eyebrow}
      </div>
      <div style={{ font: "700 30px 'Kalam'", color: 'var(--ink)', marginTop: 6 }}>{title}</div>
      {blurb && (
        <p style={{ font: "600 14px/1.7 'Quicksand'", color: 'var(--ink-body)', margin: '10px 0 22px' }}>{blurb}</p>
      )}

      <div
        style={{
          background: 'var(--surface)',
          border: '2.5px solid var(--border)',
          borderRadius: 22,
          padding: 26,
          boxShadow: '9px 10px 0 rgba(43,36,32,.10)'
        }}
      >
        {children}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 20, flexWrap: 'wrap' }}>
        {onBack && (
          <div style={secondaryButtonSmall} onClick={onBack}>
            Back
          </div>
        )}
        {onSkip && (
          <div style={secondaryButtonSmall} onClick={onSkip}>
            Skip this
          </div>
        )}
        <div style={{ flex: 1 }} />
        <div
          style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)', cursor: 'pointer', padding: '6px 4px' }}
          onClick={onFinishLater}
        >
          Finish later
        </div>
        {onNext && (
          <div style={{ ...primaryButton, opacity: nextBusy ? 0.6 : 1 }} onClick={nextBusy ? undefined : onNext}>
            {nextBusy ? 'Working…' : nextLabel}
          </div>
        )}
      </div>
    </div>
  )
}
