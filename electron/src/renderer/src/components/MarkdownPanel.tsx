import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

const components: Components = {
  h1: (props) => <h1 style={{ font: "700 30px/1.2 'Kalam'", color: 'var(--ink)', margin: '12px 0 0' }} {...props} />,
  h2: (props) => <h2 style={{ font: "700 22px 'Kalam'", color: 'var(--accent)', margin: '24px 0 0' }} {...props} />,
  h3: (props) => <h3 style={{ font: "700 18px 'Kalam'", color: 'var(--ink)', margin: '18px 0 0' }} {...props} />,
  p: (props) => <p style={{ font: "600 15px/1.75 'Quicksand'", color: 'var(--ink-body)', margin: '12px 0 0' }} {...props} />,
  ul: ({ className, ...props }) => {
    const isTaskList = className?.includes('contains-task-list')
    return (
      <ul
        className={className}
        style={{
          margin: '11px 0 0',
          paddingLeft: isTaskList ? 2 : 20,
          listStyle: isTaskList ? 'none' : undefined,
          font: "600 15px/1.9 'Quicksand'",
          color: 'var(--ink-body)'
        }}
        {...props}
      />
    )
  },
  ol: (props) => (
    <ol style={{ margin: '11px 0 0', paddingLeft: 22, font: "600 15px/1.9 'Quicksand'", color: 'var(--ink-body)' }} {...props} />
  ),
  li: (props) => <li style={{ marginBottom: 4 }} {...props} />,
  strong: (props) => <strong style={{ fontWeight: 700, color: 'var(--ink)' }} {...props} />,
  em: (props) => <em {...props} />,
  a: (props) => <a {...props} />,
  input: ({ ...props }) => (
    // GFM task-list checkboxes ("- [ ]"): render read-only, brand-accented.
    <input {...props} disabled style={{ marginRight: 8, accentColor: 'var(--accent)', verticalAlign: 'middle' }} />
  ),
  // Wrap tables so wide ones (e.g. Channel Voice Adaptation) scroll instead of
  // overflowing the panel; remark-gfm parses the `| … |` syntax into real tables.
  table: (props) => (
    <div style={{ overflowX: 'auto', margin: '14px 0 0' }}>
      <table
        style={{ borderCollapse: 'collapse', width: '100%', font: "600 13px/1.5 'Quicksand'", color: 'var(--ink-body)' }}
        {...props}
      />
    </div>
  ),
  th: (props) => (
    <th
      style={{
        border: '2px solid var(--border)',
        background: 'var(--accent-soft-bg)',
        color: 'var(--accent-deep)',
        padding: '8px 11px',
        textAlign: 'left',
        fontWeight: 700,
        whiteSpace: 'nowrap'
      }}
      {...props}
    />
  ),
  td: (props) => (
    <td style={{ border: '2px solid var(--border)', padding: '8px 11px', verticalAlign: 'top' }} {...props} />
  )
}

interface Props {
  markdown: string
}

export default function MarkdownPanel({ markdown }: Props): React.JSX.Element {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {markdown}
    </ReactMarkdown>
  )
}
