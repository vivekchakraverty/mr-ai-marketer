import { useAppStore } from '../state/store'

export default function UpdateBanner(): React.JSX.Element | null {
  const updateInfo = useAppStore((s) => s.updateInfo)
  const dismissed = useAppStore((s) => s.updateBannerDismissed)
  const dismissUpdateBanner = useAppStore((s) => s.dismissUpdateBanner)

  if (!updateInfo?.updateAvailable || dismissed) return null

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 14,
        padding: '9px 18px',
        background: 'var(--accent-soft-bg)',
        borderBottom: '2.5px solid var(--border)',
        flexShrink: 0,
        flexWrap: 'wrap'
      }}
    >
      <span style={{ font: "700 13px 'Quicksand'", color: 'var(--accent-deep)' }}>
        Mr. AI Marketer {updateInfo.latestVersion} is out — you're on {updateInfo.currentVersion}.
      </span>
      <span
        style={{
          font: "700 13px 'Quicksand'",
          color: 'var(--accent-deep)',
          textDecoration: 'underline',
          cursor: 'pointer'
        }}
        onClick={() => updateInfo.downloadUrl && window.api.openExternal(updateInfo.downloadUrl)}
      >
        Download update →
      </span>
      <span
        style={{ font: "700 15px 'Quicksand'", color: 'var(--accent-deep)', cursor: 'pointer', marginLeft: 4 }}
        onClick={dismissUpdateBanner}
      >
        ×
      </span>
    </div>
  )
}
