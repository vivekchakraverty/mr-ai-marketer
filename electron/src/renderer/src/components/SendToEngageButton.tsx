import { useAppStore, type EngageHandoff } from '../state/store'

/**
 * Hand the finished post to the composer that will actually send it.
 *
 * The three creators write a post, suggest tags for it and draw a picture to go with it —
 * and then Engage, one screen away, is where it gets posted. Until now only the words made
 * that trip: the picture had to be found again in Engage's own picker and the tags re-ticked
 * from memory, with nothing on the Engage side to say which ones had been chosen.
 *
 * So this carries all three, and to the right composer. A Mastodon draft is written against
 * that instance's character limit and its rules; dropping it into the Bluesky box would be
 * handing someone a post written for somewhere else.
 *
 * Deliberately not a send button. It fills the box and leaves the cursor there — posting
 * stays a thing the person does, having read what is about to go out.
 */

interface Props {
  network: EngageHandoff['network']
  postText: string
  /** Chosen tags, without their leading '#'. */
  tags?: string[]
  /** An /outputs URL for the generated image, if one was drawn and kept. */
  imageUrl?: string
  /** Shown on the button, e.g. "Bluesky". */
  label?: string
}

export default function SendToEngageButton({
  network,
  postText,
  tags = [],
  imageUrl = '',
  label
}: Props): React.JSX.Element | null {
  const sendToEngage = useAppStore((s) => s.sendToEngage)
  if (!postText.trim()) return null

  const carried = [
    tags.length ? `${tags.length} tag${tags.length === 1 ? '' : 's'}` : '',
    imageUrl ? 'image' : ''
  ].filter(Boolean)

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        padding: '8px 16px',
        borderRadius: 999,
        border: '2.5px solid var(--border)',
        background: 'var(--surface)',
        color: 'var(--ink-muted)',
        font: "700 12.5px 'Quicksand'",
        cursor: 'pointer'
      }}
      title={
        carried.length
          ? `Opens the ${label ?? network} composer with the post, ${carried.join(' and ')}`
          : `Opens the ${label ?? network} composer with this post`
      }
      onClick={() => sendToEngage({ text: postText, tags, imageUrl, network })}
    >
      Send to Engage{carried.length ? ` (with ${carried.join(' + ')})` : ''} →
    </div>
  )
}
