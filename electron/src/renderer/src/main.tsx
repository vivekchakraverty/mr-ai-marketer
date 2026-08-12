import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './styles/tokens.css'

/**
 * A boundary above App, not only inside it.
 *
 * App already wraps the routed screen, which handles a crash in a tool. It cannot handle a
 * crash in App itself or in the chrome around the route — the nav bar, the shelf, the queue
 * indicator — because those are its siblings or its own render. React's response to an
 * uncaught error is to unmount the entire root, and what the user gets is a blank window
 * with no message, no nav bar and nothing to copy.
 *
 * This one turns that into something readable and reportable. It should never be the
 * boundary that catches anything; that it exists at all is insurance against the case where
 * the diagnosis would otherwise be "the app went white sometimes".
 */
ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
)
