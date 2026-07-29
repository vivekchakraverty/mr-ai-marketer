import type { CSSProperties } from 'react'

export const label: CSSProperties = {
  display: 'block',
  font: "700 12px 'Quicksand'",
  color: 'var(--accent-deep)',
  letterSpacing: '.03em',
  textTransform: 'uppercase',
  marginBottom: 7
}

export const sectionEyebrow: CSSProperties = {
  font: "700 11px 'Quicksand'",
  letterSpacing: '.16em',
  textTransform: 'uppercase',
  color: 'var(--ink-faint)'
}

export const textInput: CSSProperties = {
  width: '100%',
  background: 'var(--surface)',
  border: '2px solid var(--border)',
  borderRadius: 12,
  padding: '11px 13px',
  font: "600 14px 'Quicksand'",
  color: 'var(--ink)'
}

export const textInputOnSurface: CSSProperties = {
  ...textInput,
  background: 'var(--surface)'
}

export const textarea: CSSProperties = {
  ...textInput,
  font: "600 13.5px/1.5 'Quicksand'",
  resize: 'none'
}

export const select: CSSProperties = {
  ...textInput,
  padding: '11px 30px 11px 13px',
  appearance: 'none',
  cursor: 'pointer'
}

export const primaryButton: CSSProperties = {
  background: 'var(--accent)',
  color: 'var(--accent-ink)',
  border: '2.5px solid var(--border)',
  borderRadius: 999,
  padding: 14,
  textAlign: 'center',
  font: "700 15px 'Kalam'",
  cursor: 'pointer',
  boxShadow: 'var(--shadow-sm)'
}

export const primaryButtonSmall: CSSProperties = {
  background: 'var(--accent)',
  color: 'var(--accent-ink)',
  border: '2.5px solid var(--border)',
  borderRadius: 999,
  padding: '9px 18px',
  font: "700 13.5px 'Kalam'",
  cursor: 'pointer',
  boxShadow: 'var(--shadow-sm)'
}

export const secondaryButtonSmall: CSSProperties = {
  background: 'var(--surface)',
  border: '2px solid var(--border)',
  color: 'var(--ink)',
  borderRadius: 999,
  padding: '9px 16px',
  font: "700 13px 'Quicksand'",
  cursor: 'pointer'
}

export const card: CSSProperties = {
  background: 'var(--surface)',
  border: '2.5px solid var(--border)',
  borderRadius: 22,
  padding: 26,
  boxShadow: 'var(--shadow-md)'
}

export const paperCard: CSSProperties = {
  background: 'var(--surface-paper)',
  border: '2.5px solid var(--border-paper)',
  borderRadius: 20,
  boxShadow: 'var(--shadow-paper)'
}

export const segOn: CSSProperties = { background: 'var(--accent)', color: 'var(--accent-ink)' }
export const segOff: CSSProperties = { color: 'var(--ink-muted)' }

export const segItem = (on: boolean): CSSProperties => ({
  flex: '1 1 auto',
  minWidth: 'fit-content',
  textAlign: 'center',
  padding: '8px 10px',
  borderRadius: 9,
  font: "700 12px 'Quicksand'",
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  ...(on ? segOn : segOff)
})

export const segGroup: CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: 6,
  background: 'var(--surface)',
  border: '2px solid var(--border)',
  borderRadius: 12,
  padding: 4
}

const chipOn: CSSProperties = { background: 'var(--accent)', color: 'var(--accent-ink)', borderColor: 'var(--border)' }
const chipOff: CSSProperties = { background: 'var(--surface)', color: 'var(--ink-muted)', borderColor: 'var(--border)' }

export const chip = (on: boolean): CSSProperties => ({
  padding: '7px 13px',
  borderRadius: 999,
  font: "700 12.5px 'Quicksand'",
  cursor: 'pointer',
  border: '2px solid',
  ...(on ? chipOn : chipOff)
})

export const toggleTrack = (on: boolean): CSSProperties => ({
  width: 40,
  height: 23,
  borderRadius: 12,
  background: on ? 'var(--accent)' : '#e9dcc4',
  border: '2px solid var(--border)',
  position: 'relative',
  cursor: 'pointer',
  flexShrink: 0
})

export const toggleKnob = (on: boolean): CSSProperties => ({
  position: 'absolute',
  top: 1,
  left: on ? 17 : 1,
  width: 16,
  height: 16,
  borderRadius: '50%',
  background: 'var(--surface)',
  border: '1.5px solid var(--border)'
})

export const tag: CSSProperties = {
  padding: '3px 10px',
  borderRadius: 999,
  font: "700 10.5px 'Quicksand'",
  letterSpacing: '.06em',
  textTransform: 'uppercase',
  background: 'var(--accent-soft-bg)',
  color: 'var(--accent-deep)'
}

export const pill: CSSProperties = {
  background: 'var(--surface)',
  border: '2px solid var(--border)',
  borderRadius: 999,
  padding: '5px 12px',
  font: "700 11.5px 'Quicksand'",
  color: 'var(--ink-muted)'
}

export const codeBlock: CSSProperties = {
  background: 'var(--surface-code)',
  border: '2px solid var(--border)',
  borderRadius: 14,
  padding: '16px 18px',
  font: "500 13px/1.7 ui-monospace, Menlo, monospace",
  color: 'var(--code-ink)'
}
