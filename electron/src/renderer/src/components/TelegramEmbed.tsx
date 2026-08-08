import { useRef, useState, type CSSProperties } from 'react'
import { secondaryButtonSmall } from '../styles/styleKit'

/**
 * Telegram Web, embedded.
 *
 * An Electron <webview> rather than an iframe, for the same reason the Mastodon panel in
 * Engage is one: Telegram Web sends `frame-ancestors 'none'`, so an iframe shows nothing.
 * A webview loads it as its own top-level document, which is the only way to have the real
 * client in the app rather than a read-only imitation of it.
 *
 * Its `src` is allow-listed in main/index.ts by exact hostname, and it gets no preload, no
 * node integration and its own session partition — this is someone else's page, and the
 * renderer's privileges must not reach it.
 *
 * The session is deliberately shared between the two chats (one partition, not two): they
 * are the same Telegram account, and separate partitions would mean logging in twice and
 * holding two live client sessions for no benefit.
 */
type WebviewHandle = HTMLElement & {
  reload: () => void
  getURL: () => string
}

const WebView = 'webview' as unknown as React.FC<{
  ref?: React.Ref<WebviewHandle>
  src: string
  partition: string
  allowpopups?: string
  style?: CSSProperties
}>

const PARTITION = 'persist:telegram'

/** Telegram Web's own deep-link shape for opening a chat by id. */
function chatUrl(chatId: string): string {
  const id = (chatId || '').trim()
  if (!id) return 'https://web.telegram.org/a/'
  return `https://web.telegram.org/a/#${id}`
}

interface Props {
  chatId: string
  /** Shown instead of the client when the chat has not been linked yet. */
  emptyHint: string
  height?: number
}

export default function TelegramEmbed({ chatId, emptyHint, height = 520 }: Props): React.JSX.Element {
  const ref = useRef<WebviewHandle | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  if (!chatId) {
    return (
      <div
        style={{
          border: '2px dashed var(--border)',
          borderRadius: 16,
          padding: '38px 26px',
          textAlign: 'center',
          font: "600 13px/1.7 'Quicksand'",
          color: 'var(--ink-fainter-2)'
        }}
      >
        {emptyHint}
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 8 }}>
        <div
          style={secondaryButtonSmall}
          onClick={() => {
            // Remounting rather than calling reload(): if the webview has not attached yet
            // there is nothing to reload, and a fresh mount works in both states.
            if (ref.current) ref.current.reload()
            else setReloadKey((k) => k + 1)
          }}
        >
          Reload
        </div>
        <div style={secondaryButtonSmall} onClick={() => void window.api.openExternal(chatUrl(chatId))}>
          Open in browser ↗
        </div>
      </div>
      <div
        style={{
          border: '2.5px solid var(--border)',
          borderRadius: 16,
          overflow: 'hidden',
          boxShadow: 'var(--shadow-sm)',
          background: 'var(--surface)'
        }}
      >
        <WebView
          key={reloadKey}
          ref={ref}
          src={chatUrl(chatId)}
          partition={PARTITION}
          allowpopups="true"
          style={{ width: '100%', height, display: 'flex' }}
        />
      </div>
      <div style={{ font: "600 11.5px 'Quicksand'", color: 'var(--ink-faint)', marginTop: 6 }}>
        You're signed into Telegram here as yourself, not as the bot — the first time, scan the QR code with your
        phone.
      </div>
    </div>
  )
}
