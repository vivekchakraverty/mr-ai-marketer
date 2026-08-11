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
  ),
  // Code blocks had no styling at all, so they fell through to the browser default of
  // `white-space: pre` — which cannot wrap. A model that emits a palette as JSON, or a
  // long prose line inside a fence, then pushed the whole panel wider than the column and
  // dragged the layout with it. Wrapping is the fix; the scroll is a backstop for content
  // with genuinely unbreakable runs, and it scrolls the block rather than the page.
  pre: (props) => (
    <pre
      style={{
        margin: '12px 0 0',
        padding: '12px 14px',
        background: 'var(--accent-soft-bg)',
        border: '2px solid var(--border)',
        borderRadius: 10,
        maxWidth: '100%',
        overflowX: 'auto',
        whiteSpace: 'pre-wrap',
        overflowWrap: 'anywhere',
        font: "600 12.5px/1.6 'Consolas', 'Courier New', monospace",
        color: 'var(--ink-body)'
      }}
      {...props}
    />
  ),
  code: ({ className, ...props }) => {
    // react-markdown gives fenced blocks a `language-*` class and puts them inside <pre>;
    // inline code has neither. Only the inline case wants its own chrome — styling both
    // would double the background and border inside the block above.
    const isBlock = /\blanguage-/.test(className ?? '')
    if (isBlock) return <code className={className} {...props} />
    return (
      <code
        className={className}
        style={{
          padding: '1px 5px',
          borderRadius: 5,
          background: 'var(--accent-soft-bg)',
          font: "600 0.92em 'Consolas', 'Courier New', monospace",
          overflowWrap: 'anywhere'
        }}
        {...props}
      />
    )
  }
}

/**
 * Undo a fence wrapped around an entire section.
 *
 * The hosted Space's model likes to answer with the whole section inside a ```markdown
 * fence. react-markdown is right to render that as a code block, which is why Positioning
 * Statement and Competitive Frame came out as monospace source with visible `#` and `##`
 * while neighbouring sections rendered properly.
 *
 * Only whole-content fences are unwrapped, and only when the body is not itself fenced —
 * a section that legitimately contains a JSON block keeps it.
 */
function unwrapFence(markdown: string): string {
  const text = markdown.trim()
  if (!text.startsWith('```') || !text.endsWith('```')) return markdown

  const lines = text.split('\n')
  if (lines.length < 3) return markdown

  const lang = lines[0].slice(3).trim().toLowerCase()
  // An explicit markdown fence is unambiguous. A bare ``` is only treated as a wrapper
  // when the body looks like markdown, so real unlabelled code is left alone.
  const body = lines.slice(1, -1).join('\n')
  const looksLikeMarkdown = /^\s*#{1,3}\s/m.test(body) || /^\s*[-*]\s+\S/m.test(body)
  const isWrapper = lang === 'markdown' || lang === 'md' || (lang === '' && looksLikeMarkdown)
  if (!isWrapper) return markdown

  // A fence inside the body means the outer pair was not a single wrapper.
  if (body.includes('```')) return markdown
  return body
}

interface Props {
  markdown: string
}

export default function MarkdownPanel({ markdown }: Props): React.JSX.Element {
  // The overflow fix is `white-space: pre-wrap` on `pre` above; the results column already
  // carries the `minWidth: 0` a flex child needs, so that was never the missing piece.
  // These constraints just stop any future wide child escaping the panel, and overflowWrap
  // catches long unbroken strings in ordinary prose such as a URL.
  return (
    <div style={{ minWidth: 0, maxWidth: '100%', overflowWrap: 'anywhere' }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {unwrapFence(markdown)}
      </ReactMarkdown>
    </div>
  )
}
