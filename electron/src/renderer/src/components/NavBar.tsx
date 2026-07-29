import type { CSSProperties } from 'react'
import { useAppStore } from '../state/store'
import marketerIcon from '../assets/marketer-icon.png'

const navBase: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '9px 18px',
  borderRadius: 999,
  border: '2.5px solid var(--border)',
  font: "700 14px 'Quicksand'",
  cursor: 'pointer'
}

function navStyle(active: boolean): CSSProperties {
  return {
    ...navBase,
    background: active ? 'var(--accent)' : 'var(--surface)',
    color: active ? 'var(--accent-ink)' : 'var(--ink-muted)',
    boxShadow: active ? 'var(--shadow-sm)' : 'none'
  }
}

export default function NavBar(): React.JSX.Element {
  const route = useAppStore((s) => s.route)
  const goHome = useAppStore((s) => s.goHome)
  const goResearch = useAppStore((s) => s.goResearch)
  const goCreate = useAppStore((s) => s.goCreate)
  const goEngage = useAppStore((s) => s.goEngage)
  const goAnalytics = useAppStore((s) => s.goAnalytics)
  const goDistribute = useAppStore((s) => s.goDistribute)
  const goLibrary = useAppStore((s) => s.goLibrary)
  const hfUsername = useAppStore((s) => s.hfUsername)
  const goSettings = useAppStore((s) => s.goSettings)

  return (
    <>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '14px 34px',
          background: 'var(--surface)',
          borderBottom: '2.5px solid var(--border)',
          flexShrink: 0,
          gap: 20,
          flexWrap: 'wrap',
          zIndex: 5
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 11, cursor: 'pointer' }} onClick={goHome}>
          <span
            style={{
              width: 36,
              height: 36,
              borderRadius: '52% 48% 55% 45%',
              border: '2.5px solid var(--border)',
              overflow: 'hidden',
              flexShrink: 0,
              display: 'block'
            }}
          >
            <img src={marketerIcon} alt="Mr. AI Marketer" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          </span>
          <span style={{ font: "700 20px 'Kalam'", color: 'var(--ink)' }}>Mr. AI Marketer</span>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={navStyle(route === 'home')} onClick={goHome}>
            Home
          </div>
          <div style={navStyle(route === 'research')} onClick={goResearch}>
            Research / Strategy
          </div>
          <div style={navStyle(route === 'create')} onClick={goCreate}>
            Create
          </div>
          <div style={navStyle(route === 'engage')} onClick={goEngage}>
            Engage
          </div>
          <div style={navStyle(route === 'analytics')} onClick={goAnalytics}>
            Analytics
          </div>
          <div style={navStyle(route === 'distribute')} onClick={goDistribute}>
            Distribute
          </div>
          <div style={navStyle(route === 'library')} onClick={goLibrary}>
            Library
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: 'var(--surface)',
              border: '2px solid var(--border)',
              borderRadius: 999,
              padding: '8px 14px',
              color: 'var(--ink-faint)',
              font: "600 13px 'Quicksand'"
            }}
          >
            <span style={{ width: 11, height: 11, borderRadius: '50%', border: '1.5px solid var(--ink-fainter)' }} />
            Search
          </div>
          <div
            style={{
              background: 'var(--accent)',
              color: 'var(--accent-ink)',
              border: '2.5px solid var(--border)',
              borderRadius: 999,
              padding: '9px 18px',
              font: "700 13.5px 'Quicksand'",
              cursor: 'pointer',
              boxShadow: 'var(--shadow-sm)'
            }}
            onClick={goCreate}
          >
            + New content
          </div>
          <span
            title={hfUsername ? `${hfUsername} — Settings` : 'Settings'}
            style={{
              width: 34,
              height: 34,
              borderRadius: '50%',
              background: 'var(--avatar-bg)',
              border: '2.5px solid var(--border)',
              color: 'var(--avatar-ink)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              font: "700 14px 'Quicksand'",
              cursor: 'pointer'
            }}
            onClick={goSettings}
          >
            {(hfUsername?.[0] ?? 'V').toUpperCase()}
          </span>
        </div>
      </div>

      {/* "Waterline" — a scalloped strip that reads as a row of gentle waves under the header. */}
      <div
        style={{
          height: 15,
          flexShrink: 0,
          background: 'var(--tool-plan)',
          WebkitMask: 'radial-gradient(circle at 11px 15px, transparent 8px, #000 8.5px)',
          mask: 'radial-gradient(circle at 11px 15px, transparent 8px, #000 8.5px)',
          WebkitMaskSize: '22px 15px',
          maskSize: '22px 15px',
          WebkitMaskRepeat: 'repeat-x',
          maskRepeat: 'repeat-x'
        }}
      />
    </>
  )
}
