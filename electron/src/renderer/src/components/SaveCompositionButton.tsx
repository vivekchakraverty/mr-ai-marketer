import { useEffect, useState } from 'react'
import { saveToLibrary } from '../api/client'
import { useAppStore } from '../state/store'

/**
 * Keep the whole post — words, tags and picture — as one Library entry.
 *
 * The three pieces were already saveable separately and that turned out to be the problem.
 * The draft could be saved with the Save button, the image filed itself the moment it was
 * drawn, and the hashtags were only ever on screen — so a finished post arrived in the
 * Library as two unrelated rows and a set of tags that existed nowhere. Reassembling it
 * later meant remembering which picture went with which draft.
 *
 * So this writes one row: the post text with the chosen tags appended exactly as they would
 * be published, and the generated image as the row's file. That is also the shape the
 * Library already understands — text plus one attachment — so the entry is editable in place
 * and the picture shows on the card, with nothing new taught to the Library itself.
 *
 * Separate from SaveButton rather than a flag on it. That one exists to avoid duplicating
 * what a tool already saved, and answers "is this kept?" with a libraryId. This one is
 * always a fresh write, because a composition is only finished once the person says it is —
 * and they may well say so twice, having changed the tags.
 */

interface Props {
  /** Library grouping. Social for Bluesky, and for the other two as well: an image or a
   *  post is the same kind of artefact whichever network it was written for. */
  tool: string
  title: string
  subtitle?: string
  /** The composed post. Without this there is nothing worth bundling. */
  postText: string
  /** Tags the user actually chose, without their leading '#'. */
  tags?: string[]
  /** An /outputs URL for the generated image, if one was drawn and kept. */
  imageUrl?: string
}

export default function SaveCompositionButton({
  tool,
  title,
  subtitle = '',
  postText,
  tags = [],
  imageUrl = ''
}: Props): React.JSX.Element | null {
  const goLibrary = useAppStore((s) => s.goLibrary)
  const [savedId, setSavedId] = useState('')
  const [busy, setBusy] = useState(false)

  // Editing the draft, the tags or the picture makes the saved copy no longer this one, so
  // the button offers to keep the new version rather than claiming the work is already in.
  useEffect(() => {
    setSavedId('')
  }, [postText, imageUrl, tags.join(' ')])

  if (!postText.trim()) return null

  // Appended the way they would actually be posted, so what is kept is the finished thing
  // rather than a draft plus a note about which tags were meant to go on it.
  const tagLine = tags.map((t) => (t.startsWith('#') ? t : `#${t}`)).join(' ')
  const content = tagLine ? `${postText.trim()}\n\n${tagLine}` : postText.trim()

  const parts = [
    'post',
    tags.length ? `${tags.length} tag${tags.length === 1 ? '' : 's'}` : '',
    imageUrl ? 'image' : ''
  ].filter(Boolean)

  const base: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 7,
    padding: '8px 16px',
    borderRadius: 999,
    border: '2.5px solid var(--border)',
    font: "700 12.5px 'Quicksand'",
    cursor: 'pointer'
  }

  if (savedId) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 9 }}>
        <span
          style={{ ...base, background: 'var(--tool-distribute)', color: 'var(--accent-ink)', cursor: 'default' }}
        >
          Sent to Library ✓
        </span>
        <span
          onClick={goLibrary}
          style={{ font: "700 12px 'Quicksand'", color: 'var(--accent-deep)', cursor: 'pointer' }}
        >
          Open Library →
        </span>
      </span>
    )
  }

  return (
    <span
      title={`Keeps the ${parts.join(' + ')} together as one Library entry`}
      style={{ ...base, background: 'var(--surface)', color: 'var(--ink-muted)', opacity: busy ? 0.6 : 1 }}
      onClick={
        busy
          ? undefined
          : () => {
              setBusy(true)
              void saveToLibrary({ tool, title, subtitle, content, imageUrl: imageUrl || undefined })
                .then((res) => setSavedId(res.libraryId))
                // The API client raises the error popup itself.
                .catch(() => undefined)
                .finally(() => setBusy(false))
            }
      }
    >
      {busy ? 'Sending…' : `Send to Library (${parts.join(' + ')})`}
    </span>
  )
}
