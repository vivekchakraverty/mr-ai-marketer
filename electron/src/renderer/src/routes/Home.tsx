import { useEffect, useRef, useState } from 'react'
import { fetchDistributionChannels } from '../api/client'
import { PLATFORM_SETUP_GUIDES } from '../state/platformSetupGuides'
import { useAppStore } from '../state/store'
import StatTile from '../components/StatTile'
import ToolCard from '../components/ToolCard'
import mascotVideo from '../assets/mascot-beach.mp4'

function MascotVideo(): React.JSX.Element {
  const videoRef = useRef<HTMLVideoElement | null>(null)

  function attachVideoRef(el: HTMLVideoElement | null): void {
    if (!el || videoRef.current === el) return
    videoRef.current = el
    el.loop = false
    el.playsInline = true
    el.muted = true
    void el.play().catch(() => {})
  }

  function handleReplay(): void {
    const el = videoRef.current
    if (!el) return
    el.muted = false
    el.currentTime = 0
    void el.play().catch(() => {})
  }

  return (
    <div
      style={{
        width: 220,
        height: 168,
        position: 'relative',
        flexShrink: 0,
        borderRadius: 20,
        border: '2.5px solid var(--border)',
        overflow: 'hidden',
        boxShadow: 'var(--shadow-sm)',
        background: '#8fd3f4'
      }}
    >
      <video
        ref={attachVideoRef}
        src={mascotVideo}
        onClick={handleReplay}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', cursor: 'pointer' }}
      />
    </div>
  )
}

export default function Home(): React.JSX.Element {
  const library = useAppStore((s) => s.library)
  const goResearch = useAppStore((s) => s.goResearch)
  const goLibrary = useAppStore((s) => s.goLibrary)
  const openTool = useAppStore((s) => s.openTool)
  const goDistribute = useAppStore((s) => s.goDistribute)
  const openReader = useAppStore((s) => s.openReader)
  const hfUsername = useAppStore((s) => s.hfUsername)

  // Real channel status for the Distribution card — null until (and unless) the
  // distribution engine answers, in which case the card keeps its "not connected" default.
  const [connectedChannels, setConnectedChannels] = useState<string[] | null>(null)
  useEffect(() => {
    let cancelled = false
    fetchDistributionChannels()
      .then((res) => {
        if (!cancelled && res.ready) {
          setConnectedChannels(res.channels.filter((c) => c.connected).map((c) => c.channel))
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const recent = library.slice(0, 3)
  const channelChips =
    connectedChannels && connectedChannels.length > 0
      ? connectedChannels.map((c) => PLATFORM_SETUP_GUIDES[c]?.label ?? c)
      : ['LinkedIn', 'X', 'Bluesky', 'Reddit', 'Email']

  return (
    <div style={{ maxWidth: 1240, margin: '0 auto', padding: '30px 34px 60px' }}>
      {/* HERO */}
      <div
        style={{
          position: 'relative',
          display: 'flex',
          gap: 30,
          alignItems: 'center',
          background: '#dceffb',
          border: '2.5px solid var(--border)',
          borderRadius: 28,
          padding: 30,
          boxShadow: 'var(--shadow-md)',
          marginBottom: 22,
          flexWrap: 'wrap'
        }}
      >
        <div style={{ flex: 1, minWidth: 300, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ font: "700 13.5px 'Quicksand'", letterSpacing: '.05em', textTransform: 'uppercase', color: '#3e93bf' }}>
            Studio home
          </div>
          <div style={{ font: "700 38px/1.15 'Kalam'", color: 'var(--ink)' }}>
            Kick back{hfUsername ? `, ${hfUsername}` : ''} — we've got <span style={{ color: 'var(--accent)' }}>the hustle</span>.
          </div>
          <div style={{ font: "600 15px/1.6 'Quicksand'", color: 'var(--ink-muted)', maxWidth: 420 }}>
            Research, create and ship marketing content while you sip something cold. Five tools, zero hangovers (mostly).
          </div>
        </div>
        <MascotVideo />
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 10 }}>
          <StatTile value={library.length} label="Squirreled away" />
          <StatTile value="—" label="Out in the wild" />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        <div
          style={{
            gridColumn: 'span 2',
            background: 'var(--accent)',
            border: '2.5px solid var(--border)',
            borderRadius: 22,
            padding: 26,
            boxShadow: 'var(--shadow-md)',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            minHeight: 172,
            color: 'var(--accent-ink)',
            cursor: 'pointer'
          }}
          onClick={goResearch}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div
              style={{
                width: 42,
                height: 42,
                borderRadius: '52% 48% 50% 50%',
                background: 'rgba(255,251,243,.22)',
                border: '2px solid rgba(255,251,243,.5)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center'
              }}
            >
              <div
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: '50%',
                  border: '3px solid var(--accent-ink)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center'
                }}
              >
                <div
                  style={{
                    width: 5,
                    height: 14,
                    background: 'var(--accent-ink)',
                    clipPath: 'polygon(50% 0, 100% 50%, 50% 100%, 0 50%)',
                    transform: 'rotate(35deg)'
                  }}
                />
              </div>
            </div>
            <span style={{ font: "700 11px 'Quicksand'", letterSpacing: '.14em', color: 'rgba(255,251,243,.7)' }}>
              RESEARCH / STRATEGY
            </span>
          </div>
          <div>
            <div style={{ font: "700 26px 'Kalam'" }}>Marketing Plan Generator</div>
            <div style={{ font: "600 13.5px/1.5 'Quicksand'", color: 'rgba(255,251,243,.88)', marginTop: 6, maxWidth: 380 }}>
              Turn one goal into a full channel strategy, budget split and 90-day timeline.
            </div>
          </div>
          <div style={{ font: "700 13px 'Kalam'" }}>Let's make a plan →</div>
        </div>

        <div
          style={{
            gridColumn: 'span 2',
            background: 'var(--surface-tint)',
            border: '2.5px solid var(--border)',
            borderRadius: 22,
            padding: 24,
            boxShadow: 'var(--shadow-md)',
            minHeight: 172,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            cursor: 'pointer'
          }}
          onClick={goDistribute}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ font: "700 20px 'Kalam'", color: 'var(--ink)' }}>Distribution</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, font: "700 12px 'Quicksand'", color: 'var(--ink-faint)' }}>
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: '50%',
                  background: connectedChannels && connectedChannels.length > 0 ? 'var(--tool-distribute)' : 'var(--ink-fainter)'
                }}
              />
              {connectedChannels && connectedChannels.length > 0
                ? `${connectedChannels.length} channel${connectedChannels.length === 1 ? '' : 's'} connected`
                : 'Not connected yet'}
            </div>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {channelChips.map((ch) => (
              <span
                key={ch}
                style={{
                  background: 'var(--surface)',
                  border: '2px solid var(--border)',
                  borderRadius: 999,
                  padding: '6px 13px',
                  font: "700 12px 'Quicksand'",
                  color: 'var(--ink-body)'
                }}
              >
                {ch}
              </span>
            ))}
          </div>
          <div style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-muted)' }}>
            {connectedChannels && connectedChannels.length > 0
              ? 'Manage your channels in Distribute →'
              : 'Set up your publishing channels in Distribute →'}
          </div>
        </div>

        <ToolCard icon="blog" title="Blog Writer" description="Word vomit, but tasteful and SEO-friendly." onClick={() => openTool('blog')} />
        <ToolCard icon="guest" title="Guest Posts" description="Slide into the right inboxes." onClick={() => openTool('guest')} />
        <ToolCard icon="tutorial" title="Tutorial Maker" description="So clear even your intern gets it." onClick={() => openTool('tutorial')} />
        <ToolCard icon="docu" title="DocuMaker" description="Docs that won't put anyone to sleep." onClick={() => openTool('docu')} />

        <div
          style={{
            gridColumn: 'span 4',
            background: 'var(--surface)',
            border: '2.5px solid var(--border)',
            borderRadius: 20,
            padding: '18px 24px',
            boxShadow: 'var(--shadow-md)'
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
            <span style={{ font: "700 11.5px 'Quicksand'", letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
              Fresh off the press
            </span>
            <span style={{ font: "700 12px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer' }} onClick={goLibrary}>
              View all →
            </span>
          </div>
          {recent.length === 0 && (
            <div style={{ padding: '18px 0', font: "600 13px 'Quicksand'", color: 'var(--ink-faint)' }}>
              Nothing generated yet — pick a tool above to get started.
            </div>
          )}
          {recent.map((item) => (
            <div
              key={item.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '11px 0',
                borderTop: '2px dashed var(--border-soft)',
                cursor: 'pointer'
              }}
              onClick={() => openReader(item)}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ width: 9, height: 9, background: 'var(--accent)', border: '1.5px solid var(--border)', borderRadius: 2 }} />
                <span style={{ font: "700 15px 'Kalam'", color: 'var(--ink)' }}>{item.title}</span>
                <span style={{ font: "700 11px 'Quicksand'", color: 'var(--ink-faint)', letterSpacing: '.06em', textTransform: 'uppercase' }}>
                  {item.subtitle}
                </span>
              </div>
              <span style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
                {new Date(item.created_at).toLocaleDateString()}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
