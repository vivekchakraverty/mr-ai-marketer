import { useEffect, useRef, useState } from 'react'
import theme from '../assets/hey-mr-marketer.mp3'

/**
 * The app's theme tune, and the switch for it.
 *
 * Two things this is careful about, both of which are the difference between a music button
 * that works and one that lies:
 *
 * **The button reflects the audio element, not a wish.** Chromium blocks audio that starts
 * without a user gesture, and while the main window opts out of that policy (see
 * main/index.ts), a blocked or failed play must not leave a button reading "playing" over
 * silence. So state is driven by the element's own `play`/`pause` events rather than set
 * optimistically on click.
 *
 * **Stopping is remembered.** Music that restarts itself on every launch, after being turned
 * off, is the single most irritating thing a desktop app can do. The preference is kept in
 * localStorage and honoured on the next start.
 */
const STORAGE_KEY = 'mraim.music.on'

export default function MusicToggle(): React.JSX.Element {
  const ref = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [blocked, setBlocked] = useState(false)

  // Default is on — the song is meant to play while the app runs — but an explicit "off"
  // from a previous session wins.
  const wanted = (localStorage.getItem(STORAGE_KEY) ?? 'on') === 'on'

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.volume = 0.35 // background music, not a performance

    const onPlay = (): void => {
      setPlaying(true)
      setBlocked(false)
    }
    const onPause = (): void => setPlaying(false)
    el.addEventListener('play', onPlay)
    el.addEventListener('pause', onPause)

    if (wanted) {
      // A rejected promise here means the autoplay policy refused. Not an error worth
      // showing — the button simply stays in its "off" state, and a click starts it, which
      // is a gesture the policy always accepts.
      void el.play().catch(() => setBlocked(true))
    }
    return () => {
      el.removeEventListener('play', onPlay)
      el.removeEventListener('pause', onPause)
    }
  }, [wanted])

  function toggle(): void {
    const el = ref.current
    if (!el) return
    if (playing) {
      el.pause()
      localStorage.setItem(STORAGE_KEY, 'off')
    } else {
      localStorage.setItem(STORAGE_KEY, 'on')
      void el.play().catch(() => setBlocked(true))
    }
  }

  return (
    <>
      {/* loop: the song is shorter than a working session. */}
      <audio ref={ref} src={theme} loop preload="auto" />
      <div
        onClick={toggle}
        title={blocked ? 'Play the theme tune' : playing ? 'Stop the music' : 'Play the music'}
        style={{
          width: 38,
          height: 38,
          borderRadius: '50%',
          flexShrink: 0,
          background: playing ? 'var(--accent)' : 'var(--surface)',
          border: '2.5px solid var(--border)',
          boxShadow: playing ? 'var(--shadow-sm)' : 'none',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer'
        }}
      >
        {playing ? (
          // Stop: a plain square, which reads as "stop" at this size where two bars don't.
          <span
            style={{
              width: 11,
              height: 11,
              borderRadius: 2,
              background: 'var(--accent-ink)'
            }}
          />
        ) : (
          // Play: a triangle drawn with borders, matching how the rest of the app draws
          // icons — CSS boxes rather than an SVG (see NavBar's cog).
          <span
            style={{
              width: 0,
              height: 0,
              marginLeft: 3,
              borderTop: '7px solid transparent',
              borderBottom: '7px solid transparent',
              borderLeft: '11px solid var(--ink-muted)'
            }}
          />
        )}
      </div>
    </>
  )
}
