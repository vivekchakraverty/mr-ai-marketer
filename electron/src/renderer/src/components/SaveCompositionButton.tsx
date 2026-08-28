import { useEffect, useState } from 'react'
import { saveToLibrary, updateLibraryItem } from '../api/client'
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
 * be published, and the post's attachment — the generated image, or an uploaded clip — as
 * the row's file. That is also the shape the Library already understands: text plus one
 * attachment, so the entry is editable in place and the media shows on the card, with
 * nothing new taught to the Library itself.
 *
 * The clip shares that slot rather than getting one of its own because a post carries one
 * embed; the send path refuses an image and a video together for the same reason. Carrying
 * it here is what lets a video survive Create → Library → Distribute, which it did not
 * before: the composer's clip reached Engage and nothing else, so distributing a saved post
 * meant attaching the file a second time in the send dialog.
 *
 * Separate from SaveButton rather than a flag on it. That one exists to avoid duplicating
 * what a tool already saved, and answers "is this kept?" with a libraryId. This one still
 * writes whenever asked, because a composition is only finished once the person says it is —
 * and they may well say so twice, having changed the tags.
 *
 * Where it writes is the part that took a bug to get right. The Mastodon and Bluesky
 * generators file a Library row of their own the moment they produce text, and this button
 * used to add a second one — so a single post arrived as two near-identical cards, the bare
 * generation and the finished composition. Given that row's `libraryId` it now finishes that
 * row in place instead: same entry, now carrying the tags and the picture. Saving again after
 * changing the tags updates it again rather than accumulating cards, and a fresh generation
 * brings a new libraryId and therefore a new row. Without one (Tumblr, whose generator files
 * nothing) it still creates the entry itself.
 */

interface Props {
  /** The row the generator already filed for this text, when there is one. Finished in
   *  place rather than duplicated; see the note above. */
  libraryId?: string | null
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
  /** A YouTube link chosen for the post. Kept in the text, because it is a link rather
   *  than a file and the entry's one file slot is for the real attachment. */
  videoUrl?: string
  /** An /outputs URL for an uploaded clip. Shares the entry's single file slot with the
   *  picture — safe because a post carries one embed, which is why the send path refuses
   *  an image and a video together. The picture wins if somehow both are present, since
   *  that is the pairing this button has always filed. */
  videoFileUrl?: string
}

export default function SaveCompositionButton({
  libraryId,
  tool,
  title,
  subtitle = '',
  postText,
  tags = [],
  imageUrl = '',
  videoUrl = '',
  videoFileUrl = ''
}: Props): React.JSX.Element | null {
  const goLibrary = useAppStore((s) => s.goLibrary)
  const [savedId, setSavedId] = useState('')
  const [busy, setBusy] = useState(false)

  // Editing the draft, the tags or the picture makes the saved copy no longer this one, so
  // the button offers to keep the new version rather than claiming the work is already in.
  // A new generation arrives as a new libraryId and resets it for the same reason.
  useEffect(() => {
    setSavedId('')
  }, [postText, imageUrl, videoUrl, videoFileUrl, tags.join(' '), libraryId])

  if (!postText.trim()) return null

  // Appended the way they would actually be posted, so what is kept is the finished thing
  // rather than a draft plus a note about which tags were meant to go on it.
  const tagLine = tags.map((t) => (t.startsWith('#') ? t : `#${t}`)).join(' ')
  const body = tagLine ? `${postText.trim()}\n\n${tagLine}` : postText.trim()
  // The video lives in the text because an entry carries one file and that slot is the
  // picture's. Left out, a saved post would lose it silently — the exact fragmentation this
  // button exists to end.
  const video = videoUrl.trim()
  const content = video && !body.includes(video) ? `${body}\n\n${video}` : body

  // The clip and the YouTube link are different things and are named differently, so the
  // button does not claim to be keeping a file when all it has is a link.
  const clip = !imageUrl && videoFileUrl ? videoFileUrl : ''
  const parts = [
    'post',
    tags.length ? `${tags.length} tag${tags.length === 1 ? '' : 's'}` : '',
    imageUrl ? 'image' : '',
    clip ? 'video' : '',
    video ? 'video link' : ''
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
              const written = libraryId
                ? updateLibraryItem(libraryId, {
                    title,
                    subtitle,
                    content,
                    imageUrl: imageUrl || undefined,
                    videoFileUrl: clip || undefined
                  }).then(() => libraryId)
                : saveToLibrary({
                    tool,
                    title,
                    subtitle,
                    content,
                    imageUrl: imageUrl || undefined,
                    videoFileUrl: clip || undefined
                  }).then((res) => res.libraryId)
              void written
                .then((id) => setSavedId(id))
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
