import { useAppStore } from '../state/store'

const barStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 14,
  padding: '9px 18px',
  background: 'var(--accent-soft-bg)',
  borderBottom: '2.5px solid var(--border)',
  flexShrink: 0,
  flexWrap: 'wrap'
}

const textStyle: React.CSSProperties = { font: "700 13px 'Quicksand'", color: 'var(--accent-deep)' }

const linkStyle: React.CSSProperties = {
  ...textStyle,
  textDecoration: 'underline',
  cursor: 'pointer'
}

/** "412 MB" / "1.2 GB" — sized for a progress line, so one decimal at most. */
function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  return `${Math.round(bytes / 1024 ** 2)} MB`
}

export default function UpdateBanner(): React.JSX.Element | null {
  const update = useAppStore((s) => s.updateInfo)
  const dismissed = useAppStore((s) => s.updateBannerDismissed)
  const dismissUpdateBanner = useAppStore((s) => s.dismissUpdateBanner)

  if (!update) return null

  // Downloading and ready are never dismissible: one is in progress and the other is the
  // thing the user asked for. Only the initial "there's an update" nudge can be waved away.
  if (update.phase === 'downloading') {
    const percent = update.percent ?? 0
    return (
      <div style={barStyle}>
        <span style={textStyle}>Downloading {update.latestVersion}…</span>
        <span
          style={{
            width: 180,
            height: 8,
            borderRadius: 999,
            background: 'var(--border)',
            overflow: 'hidden',
            flexShrink: 0
          }}
          // The bar is decoration; the percentage beside it is the real readout, so screen
          // readers get the number rather than a div they can't interpret.
          aria-hidden="true"
        >
          <span
            style={{
              display: 'block',
              width: `${percent}%`,
              height: '100%',
              background: 'var(--accent-deep)',
              transition: 'width 200ms linear'
            }}
          />
        </span>
        <span style={textStyle}>
          {percent}%
          {update.total
            ? ` · ${formatBytes(update.transferred ?? 0)} / ${formatBytes(update.total)}`
            : ''}
        </span>
      </div>
    )
  }

  if (update.phase === 'ready') {
    return (
      <div style={barStyle}>
        <span style={textStyle}>Mr. AI Marketer {update.latestVersion} is ready to install.</span>
        <span style={linkStyle} onClick={() => void window.api.update.install()}>
          Restart and install →
        </span>
        <span style={{ ...textStyle, opacity: 0.75 }}>or it installs next time you close the app</span>
      </div>
    )
  }

  if (update.phase === 'available' && !dismissed) {
    return (
      <div style={barStyle}>
        <span style={textStyle}>
          Mr. AI Marketer {update.latestVersion} is out — you&apos;re on {update.currentVersion}.
        </span>
        <span style={linkStyle} onClick={() => void window.api.update.download()}>
          Download update →
        </span>
        <span
          style={{ ...textStyle, font: "700 15px 'Quicksand'", cursor: 'pointer', marginLeft: 4 }}
          onClick={dismissUpdateBanner}
          role="button"
          aria-label="Dismiss update notice"
        >
          ×
        </span>
      </div>
    )
  }

  // idle / checking / up-to-date / error all stay silent here. A failed check is not worth a
  // banner across the top of the app — Settings reports it, where someone went looking.
  return null
}
