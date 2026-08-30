import { useEffect, useState } from 'react'
import { saveToLibrary, updateLibraryItem } from '../api/client'
import { useAppStore } from '../state/store'

/**
 * The Save control that sits under generated content.
 *
 * Most tools in this app already write their result to the Library as part of generating — a
 * plan, a blog post and a brand document are all in there before you look. Adding a Save
 * button that saved *again* would quietly fill the Library with duplicates, so this shows two
 * different things:
 *
 *   * given a `libraryId` (the tool already saved it), it says so and offers to open it;
 *   * without one, it actually saves, then behaves like the first case.
 *
 * The result is a Save control on every screen that generates something, which is what a user
 * expects to find, without a second copy of everything that was already kept.
 *
 * WHAT CHANGED WHEN DRAFTS BECAME EDITABLE. "Saved to Library ✓" used to be unarguable: the
 * text could not change after the tool filed it. Now the post composers let the draft be
 * edited in place, so the button can be sitting on a tick while the words on screen are not
 * the words on the shelf. It therefore tracks what it actually saved, drops back to an
 * offer when the text moves away from that, and updates the row it already has rather than
 * filing a second one — the same duplicate this component exists to prevent.
 */
interface Props {
  /** Set when the generating call already returned one. Undefined means nothing is saved. */
  libraryId?: string | null
  /** Library grouping — matches the filter chips: Plan, Blog, Brand, Docs, Email, Topics… */
  tool: string
  title: string
  subtitle?: string
  /** What to write when this has to do the saving itself. */
  content?: string
  disabled?: boolean
}

export default function SaveButton({
  libraryId,
  tool,
  title,
  subtitle = '',
  content = '',
  disabled = false
}: Props): React.JSX.Element | null {
  const goLibrary = useAppStore((s) => s.goLibrary)
  const [savedId, setSavedId] = useState<string | null>(libraryId ?? null)
  const [savedContent, setSavedContent] = useState(content)
  const [busy, setBusy] = useState(false)

  // A fresh generation replaces the previous one, so the button has to forget what it saved.
  useEffect(() => {
    setSavedId(libraryId ?? null)
    setSavedContent(content)
    // Only on a new generation: content changing on its own is an edit, which is exactly
    // what should move the button off its tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libraryId])

  // Edited since it was saved, so the tick would be claiming something untrue.
  const stale = savedId !== null && content.trim() !== savedContent.trim()

  const nothingToSave = !content.trim() && !savedId
  if (nothingToSave || disabled) return null

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

  if (savedId && !stale) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 9 }}>
        <span
          style={{
            ...base,
            background: 'var(--tool-distribute)',
            color: 'var(--accent-ink)',
            cursor: 'default'
          }}
        >
          Saved to Library ✓
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
      style={{ ...base, background: 'var(--surface)', color: 'var(--ink-muted)', opacity: busy ? 0.6 : 1 }}
      onClick={
        busy
          ? undefined
          : () => {
              setBusy(true)
              const written = savedId
                ? updateLibraryItem(savedId, { content }).then(() => savedId)
                : saveToLibrary({ tool, title, subtitle, content }).then((res) => res.libraryId)
              void written
                .then((id) => {
                  setSavedId(id)
                  setSavedContent(content)
                })
                // The API client raises the error popup itself; nothing to add here beyond
                // letting the button return to its normal state.
                .catch(() => undefined)
                .finally(() => setBusy(false))
            }
      }
    >
      {busy ? 'Saving…' : stale ? 'Save changes to Library' : 'Save to Library'}
    </span>
  )
}
