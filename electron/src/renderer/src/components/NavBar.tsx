import type { CSSProperties } from 'react'
import { useAppStore } from '../state/store'
import AccountMenu from './AccountMenu'
import marketerIcon from '../assets/marketer-icon.png'
import MusicToggle from './MusicToggle'

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

/**
 * A cog, drawn the way the rest of the app draws icons — CSS boxes rather than an SVG.
 * Four bars crossed at 45° make eight teeth; the body disc covers their middles so only
 * the tips show, and the hole is punched in the button's own background colour, which is
 * why that colour has to be passed in rather than assumed.
 */
function CogGlyph({ color, holeColor, size = 19 }: { color: string; holeColor: string; size?: number }): React.JSX.Element {
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      {[0, 45, 90, 135].map((angle) => (
        <div
          key={angle}
          style={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            width: size * 0.2,
            height: size,
            marginLeft: -(size * 0.1),
            marginTop: -(size * 0.5),
            borderRadius: size * 0.05,
            background: color,
            transform: `rotate(${angle}deg)`
          }}
        />
      ))}
      <div style={{ position: 'absolute', inset: size * 0.14, borderRadius: '50%', background: color }} />
      <div style={{ position: 'absolute', inset: size * 0.36, borderRadius: '50%', background: holeColor }} />
    </div>
  )
}

export default function NavBar(): React.JSX.Element {
  const route = useAppStore((s) => s.route)
  const goHome = useAppStore((s) => s.goHome)
  const goResearch = useAppStore((s) => s.goResearch)
  const goCreate = useAppStore((s) => s.goCreate)
  const goEngage = useAppStore((s) => s.goEngage)
  const goAnalytics = useAppStore((s) => s.goAnalytics)
  const goManage = useAppStore((s) => s.goManage)
  const goCommunity = useAppStore((s) => s.goCommunity)
  const goDistribute = useAppStore((s) => s.goDistribute)
  const goLibrary = useAppStore((s) => s.goLibrary)
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
          <div style={navStyle(route === 'manage')} onClick={goManage}>
            Manage
          </div>
          <div style={navStyle(route === 'community')} onClick={goCommunity}>
            Community
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
            title="Settings"
            style={{
              width: 38,
              height: 38,
              borderRadius: '50%',
              background: route === 'settings' ? 'var(--accent)' : 'var(--surface)',
              border: '2.5px solid var(--border)',
              boxShadow: route === 'settings' ? 'var(--shadow-sm)' : 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              flexShrink: 0
            }}
            onClick={goSettings}
          >
            <CogGlyph
              color={route === 'settings' ? 'var(--accent-ink)' : 'var(--ink-muted)'}
              holeColor={route === 'settings' ? 'var(--accent)' : 'var(--surface)'}
            />
          </div>
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
          {/* Opens the connected-accounts panel. Settings still lives on the cog; this
              answers "am I signed in to Bluesky?" without a trip through four screens. */}
          <AccountMenu />
          {/* Last in the row, so it sits in the top-right corner. */}
          <MusicToggle />
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
