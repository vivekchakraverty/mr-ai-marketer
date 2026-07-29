import ToolIcon, { type ToolIconVariant } from './ToolIcon'

interface Props {
  icon: ToolIconVariant
  title: string
  description: string
  onClick: () => void
  index?: string
  size?: 'home' | 'hub'
}

const TOOL_LINK_COLOR: Record<ToolIconVariant, string> = {
  blog: '#ff7a5c',
  guest: '#b08f00',
  tutorial: '#2c8f7f',
  docu: '#7c5fd8',
  plan: '#3e93bf',
  distribute: '#2fa366',
  social: '#c04f8d',
  mastodon: '#4a5db8',
  email: '#c9752f'
}

export default function ToolCard({ icon, title, description, onClick, index, size = 'home' }: Props): React.JSX.Element {
  const isHub = size === 'hub'
  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '2.5px solid var(--border)',
        borderRadius: isHub ? 22 : 20,
        padding: isHub ? 24 : 20,
        boxShadow: 'var(--shadow-md)',
        display: 'flex',
        flexDirection: 'column',
        gap: isHub ? 12 : 11,
        minHeight: isHub ? 170 : 150,
        cursor: 'pointer'
      }}
      onClick={onClick}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <ToolIcon variant={icon} size={isHub ? 44 : 38} />
        {index && (
          <span
            style={{
              font: "700 13px 'Kalam'",
              color: 'var(--ink-fainter)',
              display: 'inline-block',
              transform: `rotate(${Number(index) % 2 === 0 ? 6 : -7}deg)`
            }}
          >
            {index}
          </span>
        )}
      </div>
      <div>
        <div style={{ font: `700 ${isHub ? 21 : 19}px 'Kalam'`, color: 'var(--ink)' }}>{title}</div>
        <div style={{ font: `600 ${isHub ? 13.5 : 12.5}px/1.5 'Quicksand'`, color: 'var(--ink-muted)', marginTop: isHub ? 5 : 4 }}>
          {description}
        </div>
      </div>
      <div style={{ marginTop: 'auto', font: `700 ${isHub ? 13 : 12}px 'Quicksand'`, color: TOOL_LINK_COLOR[icon] }}>
        {isHub ? `Open ${title} →` : 'Open →'}
      </div>
    </div>
  )
}
