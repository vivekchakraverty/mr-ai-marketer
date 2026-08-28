import { useState } from 'react'
import { useAppStore } from '../state/store'
import BackendImage from '../components/BackendImage'
import BackendVideo from '../components/BackendVideo'
import EditableContent from '../components/EditableContent'
import SendToDistributionModal from '../components/SendToDistributionModal'
import { paperCard, primaryButtonSmall, tag } from '../styles/styleKit'

export default function Reader(): React.JSX.Element | null {
  const item = useAppStore((s) => s.readerItem)
  const closeReader = useAppStore((s) => s.closeReader)
  const [showSend, setShowSend] = useState(false)

  if (!item) return null

  // output_path is an absolute file path; /outputs is how the backend serves that tree.
  // Pictures and clips can be shown inline — a .docx has nothing to render.
  const outputs = item.output_path?.replace(/\\/g, '/') ?? ''
  const served = '/outputs/' + outputs.split('/outputs/').slice(1).join('/outputs/')
  const imageUrl = /\.(png|jpe?g|webp|gif)$/i.test(outputs) ? served : ''
  // A composed post files its clip in the same slot the picture uses, so an entry carries
  // one or the other. Recognising it here is what carries a video through to the send
  // dialog, which otherwise has no way to know the post ever had one.
  const videoFileUrl = /\.(mp4|mov|m4v|webm)$/i.test(outputs) ? served : ''

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
        {/* A generated image files itself here with the approved prompt as its text, so
            the pair stays legible: the picture above, the words that made it below. */}
        {imageUrl ? (
          <div style={{ marginTop: 16 }}>
            <BackendImage
              url={imageUrl}
              alt={item.title}
              style={{
                width: '100%',
                maxHeight: 560,
                objectFit: 'contain',
                background: 'var(--surface)',
                border: '2px solid var(--border)',
                borderRadius: 10,
                display: 'block'
              }}
            />
          </div>
        ) : videoFileUrl ? (
          <div style={{ marginTop: 16 }}>
            <BackendVideo url={videoFileUrl} alt={item.title} style={{ maxWidth: '100%' }} />
          </div>
        ) : (
          item.output_path && (
            <p style={{ font: "600 13px/1.7 'Quicksand'", color: 'var(--ink-faint)', margin: '14px 0 0' }}>
              This entry has a document alongside it, produced by the tool that made it. What
              you edit here is the saved text — the file itself is left untouched.
            </p>
          )
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
          defaultImageUrl={imageUrl}
          defaultVideoFileUrl={videoFileUrl}
          onClose={() => setShowSend(false)}
        />
      )}
    </div>
  )
}
