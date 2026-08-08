import planBackdropVideo from '../assets/plan-beach-backdrop.mp4'
import brandBackdropVideo from '../assets/brand-beach-backdrop.mp4'
import scoutBackdropVideo from '../assets/scout-beach-backdrop.mp4'
import leadsBackdropVideo from '../assets/leads-beach-backdrop.mp4'
import influencersBackdropVideo from '../assets/influencers-beach-backdrop.mp4'
import blogBackdropVideo from '../assets/blog-beach-backdrop.mp4'
import tutorialBackdropVideo from '../assets/tutorial-beach-backdrop.mp4'
import docuBackdropVideo from '../assets/docu-beach-backdrop.mp4'
import guestBackdropVideo from '../assets/guest-beach-backdrop.mp4'
import socialBackdropVideo from '../assets/social-beach-backdrop.mp4'
import emailBackdropVideo from '../assets/email-beach-backdrop.mp4'
import mastodonBackdropVideo from '../assets/mastodon-beach-backdrop.mp4'
import engageBackdropVideo from '../assets/engage-beach-backdrop.mp4'
import outreachBackdropVideo from '../assets/outreach-beach-backdrop.mp4'
import emailTrackBackdropVideo from '../assets/emailtrack-beach-backdrop.mp4'
import blueskyBackdropVideo from '../assets/bluesky-beach-backdrop.mp4'

/**
 * Full-bleed video wallpaper for a tool screen.
 *
 * A negative z-index keeps it under every in-flow element — cards, nav, banner — so
 * nothing else needs z-index plumbing, while still painting over the body's cream.
 * The video is heavily desaturated, warmed back towards the paper palette and
 * cream-washed so it reads as background rather than as playing video; the tokens.css
 * dot grid is redrawn on top so the paper texture carries straight across it.
 *
 * `brightness` is per-video: a dusk scene needs more lift than a midday one to sit at
 * the same subdued weight.
 */
export const BACKDROP_VIDEOS = {
  plan: { src: planBackdropVideo, brightness: 1.1 },
  brand: { src: brandBackdropVideo, brightness: 1.42 },
  scout: { src: scoutBackdropVideo, brightness: 1.2 },
  leads: { src: leadsBackdropVideo, brightness: 1.38 },
  influencers: { src: influencersBackdropVideo, brightness: 0.9 },
  blog: { src: blogBackdropVideo, brightness: 1.05 },
  mastodon: { src: mastodonBackdropVideo, brightness: 1.05 },
  engage: { src: engageBackdropVideo, brightness: 1.05 },
  outreach: { src: outreachBackdropVideo, brightness: 1.15 },
  // Analytics' Email tab — distinct from `email`, which is the Email Writer tool.
  emailtrack: { src: emailTrackBackdropVideo, brightness: 1.05 },
  bluesky: { src: blueskyBackdropVideo, brightness: 1.12 },
  guest: { src: guestBackdropVideo, brightness: 1.3 },
  email: { src: emailBackdropVideo, brightness: 1.25 },
  // The night scenes. Counter-intuitively they want a modest lift, not a big one: their
  // subjects are lit against a dark sky, so a large multiplier blows them out long before
  // the sky stops reading as dark.
  tutorial: { src: tutorialBackdropVideo, brightness: 1.2 },
  docu: { src: docuBackdropVideo, brightness: 1.2 },
  social: { src: socialBackdropVideo, brightness: 1.2 }
} as const

export default function ScreenBackdrop({ video }: { video: keyof typeof BACKDROP_VIDEOS }): React.JSX.Element {
  const { src, brightness } = BACKDROP_VIDEOS[video]

  function attachVideoRef(el: HTMLVideoElement | null): void {
    if (!el) return
    el.loop = true
    el.muted = true
    el.playsInline = true
    // A looping wallpaper is the exact thing reduced-motion asks us not to do — hold frame one.
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    void el.play().catch(() => {})
  }

  return (
    <div aria-hidden style={{ position: 'fixed', inset: 0, zIndex: -1, overflow: 'hidden', pointerEvents: 'none' }}>
      <video
        // Keyed on the video so a prop swap remounts the element rather than re-pointing a
        // playing one, which would hold the outgoing video's last decoded frame on screen.
        key={video}
        ref={attachVideoRef}
        src={src}
        preload="auto"
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          filter: `grayscale(.8) sepia(.24) brightness(${brightness}) contrast(.84)`,
          opacity: 0.5
        }}
      />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          // Three layers, topmost first: extra cream over the header band, the overall
          // wash/vignette, then the dot grid. The header band is what keeps the eyebrow,
          // title and subtitle legible no matter which frame lands behind them — a video
          // whose dark passage drifts under that text would otherwise fight it.
          backgroundImage:
            'linear-gradient(to bottom, rgba(255,246,234,.72) 0px, rgba(255,246,234,.5) 150px, rgba(255,246,234,0) 340px),' +
            ' radial-gradient(ellipse at 50% 40%, rgba(255,246,234,.34) 0%, rgba(255,246,234,.6) 55%, rgba(255,246,234,.9) 100%),' +
            ' radial-gradient(#f0d9be 1.6px, transparent 1.6px)',
          backgroundSize: 'auto, auto, 26px 26px'
        }}
      />
    </div>
  )
}
