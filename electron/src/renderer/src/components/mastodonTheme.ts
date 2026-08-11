import quicksandUrl from '../assets/fonts/quicksand-variable.woff2'

/**
 * Restyle the embedded Mastodon web app to match the rest of Mr. AI Marketer.
 *
 * The embed is a real Mastodon instance in an Electron <webview>, so it arrives in
 * Mastodon's own dark theme and looks like a different application pasted into the middle
 * of this one. This maps it onto the app's palette.
 *
 * It is applied with `webview.insertCSS()` rather than by injecting a <style> element,
 * and that is not a stylistic preference — Mastodon serves a `style-src` CSP with no
 * usable nonce, so an injected <style> is blocked outright: the element lands in the DOM
 * and its `.sheet` is null. `insertCSS` goes in at the Blink level, below CSP, which is
 * what makes this possible at all.
 *
 * Most of the work is done by overriding Mastodon's own CSS custom properties, of which
 * modern versions define around three dozen covering surfaces, borders, inputs, dropdowns
 * and modals. Riding on those rather than on class names is what should keep this alive
 * across Mastodon releases: variables are a deliberate theming surface, class names are
 * internals. Text colours are the exception — those are literal in Mastodon's stylesheet,
 * so they need explicit rules, and those are the parts most likely to need revisiting
 * after an upstream redesign.
 *
 * If it ever does drift, the embed's "Match app theme" toggle turns all of it off and
 * gives back untouched Mastodon.
 */

// Kept in step with styles/tokens.css by hand. Importing the tokens is not an option:
// this string is handed to another document, which has never heard of our variables.
const INK = '#4a4235'
const INK_STRONG = '#2b2420'
const INK_FAINT = '#8a7b68'
const BG = '#fff6ea'
const SURFACE = '#fffbf3'
const SURFACE_TINT = '#fff1d6'
const BORDER_SOFT = '#ead9c2'
const ACCENT = '#ff7a5c'
const ACCENT_HOVER = '#e85f40'
const ACCENT_DEEP = '#b0553b'
const ACCENT_SOFT = '#ffe3d9'

/**
 * The font is inlined as a data: URL because a `file://` reference from the packaged
 * renderer is not something the embedded page can resolve — it is a different origin with
 * its own idea of where relative URLs point. 28 KB of woff2 is cheap next to that problem.
 */
async function fontFace(): Promise<string> {
  try {
    const res = await fetch(quicksandUrl)
    const buf = await res.arrayBuffer()
    let binary = ''
    const bytes = new Uint8Array(buf)
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
    const b64 = btoa(binary)
    return `@font-face{font-family:'MrAIMQuicksand';src:url(data:font/woff2;base64,${b64}) format('woff2');font-weight:300 700;font-display:swap}`
  } catch {
    // Without the font the palette still applies, which is the bulk of the effect.
    return ''
  }
}

function rules(): string {
  return `
:root{
  --background-color:${BG};
  --background-border-color:${BORDER_SOFT};
  --background-color-tint:rgba(255,246,234,.92);
  --surface-background-color:${SURFACE};
  --surface-border-color:${BORDER_SOFT};
  --surface-variant-background-color:${SURFACE_TINT};
  --surface-variant-active-background-color:${ACCENT_SOFT};
  --on-surface-color:rgba(255,241,214,.5);
  --input-background-color:${SURFACE};
  --input-placeholder-color:#b0a488;
  --on-input-color:${INK_STRONG};
  --dropdown-background-color:${SURFACE};
  --dropdown-border-color:${BORDER_SOFT};
  --dropdown-shadow:3px 4px 0 rgba(43,36,32,.2);
  --modal-background-color:rgba(43,36,32,.45);
  --modal-border-color:${BORDER_SOFT};
  --modal-background-variant-color:rgba(255,241,214,.94);
  --indigo-3:${ACCENT};
  --indigo-5:${ACCENT_DEEP};
  --indigo-6:${INK};
  --nested-card-background:rgba(255,122,92,.06);
  --nested-card-border:1px solid rgba(255,122,92,.22);
  --nested-card-text:${INK};
  --avatar-border-radius:10px;
  --media-outline-color:rgba(43,36,32,.14);
}

/* Mastodon hardcodes its text colours, so variables alone leave white-on-cream. */
body,.column,.column-header,.status,.status__content,.status__content p,
.account__display-name,.navigation-panel a,.column-link,.detailed-status,
.compose-form,.compose-form .autosuggest-textarea__textarea,.drawer,
.account__header__content,.notification,.dropdown-menu__item a,
.search__input,.reply-indicator__content{color:${INK}!important}

.column-header__title,.column-header>button,h1,h2,h3,h4,
.status__display-name strong,.account__display-name strong,
.navigation-panel__logo,.column-link__icon{color:${INK_STRONG}!important}

.status__relative-time,.display-name__account,.status__action-bar,
.column-header__setting-btn,.icon-button,.account__header__bio .account__header__fields dt{
  color:${INK_FAINT}!important}

a,.status__content a,.mention{color:${ACCENT_DEEP}!important}

/* The primary action keeps light text — this is the one place white is correct. */
.button,.button:active,.button:focus,.compose-form__submit .button{
  background:${ACCENT}!important;color:${SURFACE}!important;
  border-radius:999px!important;font-weight:700!important;border:2px solid ${INK_STRONG}!important}
.button:hover{background:${ACCENT_HOVER}!important}
.button.button-secondary{background:transparent!important;color:${INK_STRONG}!important}

.status{border-bottom:2px solid ${BORDER_SOFT}!important}
.column,.drawer__inner,.search__input,.dropdown-menu{border-radius:12px!important}

::-webkit-scrollbar{background:transparent!important;width:10px}
::-webkit-scrollbar-thumb{background:${BORDER_SOFT}!important;border-radius:999px!important}

body,.column,.status__content,.button,.column-header,input,textarea,
.navigation-panel a,.dropdown-menu__item a{
  font-family:'MrAIMQuicksand','Quicksand',system-ui,sans-serif!important}
`
}

/** The full stylesheet, font included. Built once and reused. */
let cached: string | null = null

export async function mastodonThemeCss(): Promise<string> {
  if (cached === null) cached = (await fontFace()) + rules()
  return cached
}
