import { Component, type ErrorInfo, type ReactNode } from 'react'
import { reportError } from '../state/errors'

/**
 * Catches a crash during render.
 *
 * Without one of these, a thrown error inside a component unmounts the whole React tree and
 * the user is left looking at a blank window with no idea what happened — the single worst
 * failure mode a desktop app has. The popup cannot help there either, because the popup is
 * itself part of the tree that just went away.
 *
 * So this renders its own minimal message rather than relying on the modal, and *also*
 * reports so the text is copyable the moment the user navigates somewhere that works.
 *
 * A class component because that is the only way React exposes this; there is no hook
 * equivalent of componentDidCatch.
 */
interface Props {
  children: ReactNode
}

interface State {
  message: string
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { message: '' }

  static getDerivedStateFromError(error: Error): State {
    return { message: error.message || String(error) }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportError({
      message: error.message || String(error),
      source: 'render',
      detail: [error.stack, info.componentStack].filter(Boolean).join('\n\n')
    })
  }

  render(): ReactNode {
    if (!this.state.message) return this.props.children
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 14,
          height: '100%',
          padding: 40,
          textAlign: 'center'
        }}
      >
        <div style={{ font: "700 22px 'Kalam'", color: 'var(--ink)' }}>This screen stopped working</div>
        <div
          style={{
            font: "600 13px/1.6 'Quicksand'",
            color: 'var(--ink-muted)',
            maxWidth: 520,
            overflowWrap: 'anywhere'
          }}
        >
          {this.state.message}
        </div>
        <div
          onClick={() => this.setState({ message: '' })}
          style={{
            padding: '9px 22px',
            borderRadius: 999,
            border: '2.5px solid var(--border)',
            background: 'var(--accent)',
            color: 'var(--accent-ink)',
            font: "700 13px 'Quicksand'",
            cursor: 'pointer'
          }}
        >
          Try again
        </div>
        <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)' }}>
          Use another tab in the header if this keeps happening — the error is copyable from the
          popup that appeared with it.
        </div>
      </div>
    )
  }
}
