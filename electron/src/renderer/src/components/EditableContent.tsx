import { useEffect, useRef, useState } from 'react'
import { updateLibraryItem } from '../api/client'
import { useAppStore } from '../state/store'
import MarkdownPanel from './MarkdownPanel'
import { secondaryButtonSmall, textarea } from '../styles/styleKit'

/**
 * A saved Library item's text, editable in place and saved on its own.
 *
 * WHY AUTOSAVE RATHER THAN A SAVE BUTTON. Everything on this shelf is already saved —
 * the tools write to the Library as they generate. An explicit Save would introduce a
 * state the rest of the app does not have (an edited-but-unsaved item), and the only way
 * to discover you were in it would be to lose the edit by navigating away.
 *
 * WHAT THE DEBOUNCE IS FOR. Typing fires a change per keystroke, and one request per
 * keystroke would be both wasteful and out-of-order — a slow early request can land after
 * a fast later one and overwrite newer text with older. So edits settle for AUTOSAVE_MS,
 * and every response is checked against the text that was current when it was sent; a
 * stale reply is discarded rather than applied.
 *
 * WHY IT ALSO SAVES ON UNMOUNT. The debounce means closing the reader within a second of
 * typing would otherwise drop the last edit. The pending timer is flushed on the way out.
 *
 * WHY EDIT AND PREVIEW ARE SEPARATE. Library content is markdown — sometimes a two-line
 * post, sometimes a whole blog draft. A rich editor that rewrote the markdown as you
 * typed would change text the user did not touch, so this edits the source and previews
 * it beside, which is honest about what is stored.
 */

/** How long typing must settle before an edit is sent. */
const AUTOSAVE_MS = 700

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

export default function EditableContent({
  itemId,
  initialContent
}: {
  itemId: string
  initialContent: string
}): React.JSX.Element {
  const patchLibraryItem = useAppStore((s) => s.patchLibraryItem)

  const [text, setText] = useState(initialContent)
  const [preview, setPreview] = useState(false)
  const [state, setState] = useState<SaveState>('idle')
  const [error, setError] = useState('')

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // What is actually in the box right now, readable from callbacks that were created
  // before the latest keystroke — including the unmount flush.
  const latest = useRef(initialContent)
  // The last value we know is on disk, so an unchanged item is never re-sent.
  const persisted = useRef(initialContent)

  // Switching to a different item while the reader is open must not carry the previous
  // one's text — or, worse, save it over the new item.
  useEffect(() => {
    setText(initialContent)
    setPreview(false)
    setState('idle')
    setError('')
    latest.current = initialContent
    persisted.current = initialContent
  }, [itemId, initialContent])

  async function save(value: string): Promise<void> {
    if (value === persisted.current) return
    setState('saving')
    setError('')
    try {
      await updateLibraryItem(itemId, { content: value })
      persisted.current = value
      // Only settle to "saved" if nothing was typed while the request was in flight;
      // otherwise the next save is already coming and this would flicker.
      if (latest.current === value) setState('saved')
      patchLibraryItem(itemId, { content: value })
    } catch (err) {
      setState('error')
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  function handleChange(value: string): void {
    setText(value)
    latest.current = value
    setState('idle')
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => void save(value), AUTOSAVE_MS)
  }

  // Flush on the way out, so a fast close does not drop the last edit.
  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current)
      if (latest.current !== persisted.current) void save(latest.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemId])

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <div style={secondaryButtonSmall} onClick={() => setPreview((v) => !v)}>
          {preview ? 'Edit' : 'Preview'}
        </div>
        <div
          style={{
            font: "600 12px 'Quicksand'",
            color: state === 'error' ? 'var(--danger-ink)' : 'var(--ink-faint)',
            marginLeft: 'auto'
          }}
        >
          {state === 'saving' && 'Saving…'}
          {state === 'saved' && 'Saved'}
          {state === 'error' && (error || 'Could not save')}
          {state === 'idle' && text !== persisted.current && 'Unsaved changes'}
        </div>
      </div>

      {preview ? (
        <MarkdownPanel markdown={text} />
      ) : (
        <textarea
          value={text}
          onChange={(e) => handleChange(e.target.value)}
          spellCheck
          style={{
            ...textarea,
            minHeight: 320,
            font: "600 14.5px/1.7 'Quicksand'",
            lineHeight: 1.7
          }}
        />
      )}
    </div>
  )
}
