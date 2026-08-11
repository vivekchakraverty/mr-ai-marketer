// `?inline` makes Vite emit the font as a base64 data: URL at build time.
//
// The previous version imported the asset URL and fetched it at runtime to build the same
// string. That works under the dev server and silently fails in a packaged build, because
// the renderer runs from file:// there and Chromium refuses to fetch file:// URLs — so the
// @font-face never arrived, font-family fell through to a font the guest page has never
// heard of, and the embed kept Mastodon's system sans.
import quicksandDataUrl from '../assets/fonts/quicksand-variable.woff2?inline'

/**
 * Restyle the embedded Mastodon web app to match the rest of Mr. AI Marketer.
 *
 * The embed is a real Mastodon instance in an Electron <webview>, so it arrives in
 * Mastodon's own dark theme and looks like a different application pasted into this one.
 *
 * Two things about how this has to be applied, both learned the hard way:
 *
 * 1. It goes in with `webview.insertCSS()`, not an injected <style> element. Mastodon
 *    serves a `style-src` CSP with no usable nonce, so a <style> is dropped silently —
 *    the element sits in the DOM with a null `.sheet` and nothing appears in the console.
 *
 * 2. Every declaration carries `!important`, including the custom properties. insertCSS
 *    inserts ahead of the page's own stylesheets rather than after them, so a plain
 *    `:root { --background-color: … }` loses to Mastodon's identical selector on source
 *    order. The first cut of this file learned that in the worst way: the `!important`
 *    rules recoloured the text for a cream background, the variable overrides did not
 *    arrive, and the result was coral text on near-black.
 *
 * Custom properties still carry most of the theme, because they are a deliberate theming
 * surface and should outlive class names across Mastodon releases. But they are a
 * convenience here, not the mechanism — the explicit background and colour rules below are
 * what actually guarantee the light surface, so a Mastodon version that renamed every
 * variable would still come out readable.
 */

// Kept in step with styles/tokens.css by hand — the guest page has never heard of our
// variables, so these have to be literals.
const BG = '#fff6ea' // --bg, the app's screen background
const SURFACE = '#fffbf3' // --surface, cards and columns
const SURFACE_TINT = '#fff1d6' // --surface-tint
const BORDER_SOFT = '#ead9c2'
const INK = '#4a4235' // --ink-body, the app's general text colour
const INK_STRONG = '#2b2420' // --ink
const INK_MUTED = '#6b5f4e'
const INK_FAINT = '#8a7b68'
const ACCENT = '#ff7a5c'
const ACCENT_HOVER = '#e85f40'
const ACCENT_DEEP = '#b0553b'
const ACCENT_SOFT = '#ffe3d9'

// The app's general body text: styleKit uses 600 15px/1.75 Quicksand in --ink-body.
const FONT = `'MrAIMQuicksand',system-ui,sans-serif`
const TEXT_SIZE = '15px'

const FONT_FACE = `@font-face{font-family:'MrAIMQuicksand';src:url(${quicksandDataUrl}) format('woff2');font-weight:300 700;font-display:swap}`

/** Everything that should sit on the app's page background. */
const PAGE_SURFACES = [
  'html',
  'body',
  '.app-body',
  '#mastodon',
  '.ui',
  '.columns-area',
  '.columns-area__panels',
  '.columns-area__panels__main',
  '.columns-area__panels__pane__inner',
  '.drawer__inner',
  '.drawer__pager'
].join(',')

/** Cards, columns and anything Mastodon draws as a raised surface. */
const CARD_SURFACES = [
  '.column',
  '.column-header',
  '.column-header__button',
  '.column-back-button',
  '.scrollable',
  '.status',
  '.detailed-status',
  '.detailed-status__action-bar',
  '.account__header__bar',
  '.notification',
  '.compose-form',
  '.compose-form__highlightable',
  '.dropdown-menu',
  '.search__input',
  '.account__header__tabs__buttons'
].join(',')

/** Text that should read as ordinary app body copy. */
const BODY_TEXT = [
  'body',
  '.column',
  '.status',
  '.status__content',
  '.status__content p',
  '.detailed-status',
  '.notification',
  '.account__header__content',
  '.compose-form',
  '.compose-form .autosuggest-textarea__textarea',
  '.reply-indicator__content',
  '.dropdown-menu__item a',
  '.column-link',
  '.navigation-panel a',
  '.account__display-name',
  '.search__input',
  '.poll__option__text',
  '.account__header__fields dd'
].join(',')

const STRONG_TEXT = [
  '.column-header__title',
  '.column-header > button',
  'h1',
  'h2',
  'h3',
  'h4',
  '.status__display-name strong',
  '.account__display-name strong',
  '.navigation-panel__logo',
  '.detailed-status__display-name strong'
].join(',')

const FAINT_TEXT = [
  '.status__relative-time',
  '.display-name__account',
  '.column-header__setting-btn',
  '.account__header__fields dt',
  '.poll__footer',
  '.status__action-bar__counter__label'
].join(',')

function rules(): string {
  return `
:root,html,body{
  color-scheme:light!important;
  --background-color:${BG}!important;
  --background-border-color:${BORDER_SOFT}!important;
  --background-color-tint:rgba(255,246,234,.92)!important;
  --surface-background-color:${SURFACE}!important;
  --surface-border-color:${BORDER_SOFT}!important;
  --surface-variant-background-color:${SURFACE_TINT}!important;
  --surface-variant-active-background-color:${ACCENT_SOFT}!important;
  --on-surface-color:rgba(255,241,214,.5)!important;
  --input-background-color:${SURFACE}!important;
  --input-placeholder-color:#b0a488!important;
  --on-input-color:${INK_STRONG}!important;
  --dropdown-background-color:${SURFACE}!important;
  --dropdown-border-color:${BORDER_SOFT}!important;
  --dropdown-shadow:3px 4px 0 rgba(43,36,32,.2)!important;
  --modal-background-color:rgba(43,36,32,.45)!important;
  --modal-border-color:${BORDER_SOFT}!important;
  --modal-background-variant-color:rgba(255,241,214,.94)!important;
  --indigo-3:${ACCENT}!important;
  --indigo-5:${ACCENT_DEEP}!important;
  --indigo-6:${INK}!important;
  --nested-card-background:rgba(255,122,92,.06)!important;
  --nested-card-border:1px solid rgba(255,122,92,.22)!important;
  --nested-card-text:${INK}!important;
  --avatar-border-radius:10px!important;
  --media-outline-color:rgba(43,36,32,.14)!important;
}

/* The light surface, stated outright rather than left to the variables above. This is what
   makes the embed readable even if a Mastodon release renames every custom property. */
${PAGE_SURFACES}{background:${BG}!important}
${CARD_SURFACES}{background:${SURFACE}!important}
.column-header,.status,.detailed-status{border-color:${BORDER_SOFT}!important}
.status{border-bottom:2px solid ${BORDER_SOFT}!important}

/* Text: the app's own body copy — Quicksand, 15px, --ink-body. */
${BODY_TEXT}{color:${INK}!important;font-family:${FONT}!important}
.status__content,.status__content p,.detailed-status .status__content{
  font-size:${TEXT_SIZE}!important;line-height:1.75!important}
${STRONG_TEXT}{color:${INK_STRONG}!important;font-family:${FONT}!important}
${FAINT_TEXT}{color:${INK_FAINT}!important}
.column-link,.navigation-panel a{font-weight:600!important}

/* Links in written content read as links; navigation does not, exactly as in the app. */
.status__content a,.account__header__content a,.reply-indicator__content a,
.status__content .mention,.hashtag{color:${ACCENT_DEEP}!important}
.column-link,.navigation-panel a,.dropdown-menu__item a{color:${INK}!important}
.column-link:hover,.navigation-panel a:hover{color:${ACCENT_DEEP}!important}
.column-link.active,.column-link--transparent.active{color:${ACCENT_DEEP}!important}

/* The primary action is the one place light text is correct. */
.button,.button:active,.button:focus,.compose-form__submit .button{
  background:${ACCENT}!important;color:${SURFACE}!important;
  border:2px solid ${INK_STRONG}!important;border-radius:999px!important;
  font-family:${FONT}!important;font-weight:700!important}
.button:hover{background:${ACCENT_HOVER}!important}
.button.button-secondary{background:transparent!important;color:${INK_STRONG}!important}

/* Icon buttons default to a pale grey that vanishes on cream. */
.icon-button{color:${INK_MUTED}!important}
.icon-button:hover{color:${ACCENT_DEEP}!important}
.icon-button.active{color:${ACCENT}!important}

input,textarea,.search__input{
  background:${SURFACE}!important;color:${INK_STRONG}!important;
  font-family:${FONT}!important}
::placeholder{color:#b0a488!important}

.column,.drawer__inner,.search__input,.dropdown-menu{border-radius:12px!important}
::-webkit-scrollbar{background:transparent!important;width:10px!important}
::-webkit-scrollbar-thumb{background:${BORDER_SOFT}!important;border-radius:999px!important}
::-webkit-scrollbar-track{background:transparent!important}
`
}

let cached: string | null = null

/** The full stylesheet, font included. Built once and reused. */
export function mastodonThemeCss(): string {
  if (cached === null) cached = FONT_FACE + rules()
  return cached
}
