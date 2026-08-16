import { useState } from 'react'
import { useAppStore } from '../state/store'
import EditableContent from '../components/EditableContent'
import SendToDistributionModal from '../components/SendToDistributionModal'
import { paperCard, primaryButtonSmall, tag } from '../styles/styleKit'

export default function Reader(): React.JSX.Element | null {
  const item = useAppStore((s) => s.readerItem)
  const closeReader = useAppStore((s) => s.closeReader)
  const [showSend, setShowSend] = useState(false)

  if (!item) return null

  return (
    <div style={{ maxWidth: 840, margin: '0 auto', padding: '26px 34px 60px' }}>
      <div style={{ font: "700 13px 'Quicksand'", color: 'var(--accent)', cursor: 'pointer', marginBottom: 16 }} onClick={closeReader}>
        ← Back
      </div>
      <div style={{ ...paperCard, padding: '42px 50px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={tag}>{item.tool}</span>
          <span style={{ font: "600 12.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
            {item.subtitle} · {new Date(item.created_at).toLocaleString()}
          </span>
        </div>
        <h1 style={{ font: "700 32px/1.2 'Kalam'", color: 'var(--ink)', margin: '14px 0 0' }}>{item.title}</h1>
        {item.output_path && (
          <p style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-faint)', margin: '14px 0 0' }}>
            This entry has a document alongside it, produced by the tool that made it. What
            you edit here is the saved text — the file itself is left untouched.
          </p>
        )}
        {/* Editing is inline and saves itself; there is no separate editor to open, and
            no unsaved state to lose by navigating away. */}
        <EditableContent
          key={item.id}
          itemId={item.id}
          initialContent={item.content ?? ''}
        />
        <div style={{ display: 'flex', gap: 10, marginTop: 24, paddingTop: 20, borderTop: '2px dashed var(--border-soft)' }}>
          <div style={primaryButtonSmall} onClick={() => setShowSend(true)}>
            Send to Distribution →
          </div>
        </div>
      </div>
      {showSend && (
        <SendToDistributionModal
          libraryItemId={item.id}
          title={item.title}
          defaultText={item.content ?? ''}
          onClose={() => setShowSend(false)}
        />
      )}
    </div>
  )
}
