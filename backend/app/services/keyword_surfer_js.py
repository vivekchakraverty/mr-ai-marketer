"""JavaScript lifted verbatim from the standalone Keyword Surfer Collector.

Kept as source strings rather than rewritten in Python because both run *inside the page*,
where Python cannot go. The capture function walks the DOM (including shadow roots) scoring
candidate elements for the Surfer panel; the focus-mode script hides Google's own chrome so
the visible window shows the search bar and the Surfer panel and little else.

Generated from V:/keyword surfer/src by scripts in the scratchpad — if that tool is updated,
re-extract rather than hand-editing, since the value here is in details like the row scoring
and the panel heuristics that are easy to diverge from by accident.
"""

# Scored against page text when hunting for the Surfer panel.
HEADER_TERMS = ['keyword ideas', 'search volume', 'similar keywords', 'keyword surfer', 'similarity', 'content ideas']

# Runs in each frame. Returns a raw snapshot: candidate rows, the panel text, any inline
# metrics from the Surfer-enhanced search bar, and diagnostics.
CAPTURE_JS = r"""({ headerTerms }) => {
    const normalize = (value) => String(value || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
    const visible = (element) => {
      if (!(element instanceof Element)) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
    };

    const allElements = [];
    const visit = (root) => {
      for (const element of root.querySelectorAll('*')) {
        allElements.push(element);
        if (element.shadowRoot) visit(element.shadowRoot);
      }
    };
    visit(document);

    const candidates = [];
    for (const element of allElements) {
      if (!visible(element)) continue;
      const marker = normalize(
        `${element.id || ''} ${element.className || ''} ${element.getAttribute('data-testid') || ''} ${element.getAttribute('aria-label') || ''}`,
      ).toLowerCase();
      const text = normalize(element.innerText || element.textContent || '');
      const lowerText = text.toLowerCase();
      const focusPanel = element.hasAttribute('data-ks-focus-panel');
      const markerMatch = focusPanel || /surfer|keyword.?ideas|similar.?keywords/.test(marker);
      const headerHits = headerTerms.filter((term) => lowerText.includes(term)).length;
      if (!markerMatch && headerHits === 0) continue;

      const rect = element.getBoundingClientRect();
      const reasonableSize = rect.width >= 180 && rect.width <= 900 && rect.height >= 40;
      const score = (focusPanel ? 100 : 0) + (markerMatch ? 9 : 0) + headerHits * 3 + (reasonableSize ? 3 : 0) + Math.min(text.length / 1000, 4);
      candidates.push({ element, score, textLength: text.length });
    }

    candidates.sort((left, right) => right.score - left.score || right.textLength - left.textLength);
    let root = candidates[0]?.element || null;

    if (root && normalize(root.innerText || root.textContent || '').length < 80) {
      let parent = root.parentElement;
      while (parent && parent !== document.body) {
        const rect = parent.getBoundingClientRect();
        const text = normalize(parent.innerText || parent.textContent || '');
        if (rect.width >= 200 && rect.width <= 900 && text.length >= 80 && text.length <= 30_000) {
          root = parent;
          break;
        }
        parent = parent.parentElement;
      }
    }

    const scopeElements = root
      ? [root, ...root.querySelectorAll('*')].filter(visible)
      : allElements.filter((element) => visible(element) && /surfer/i.test(`${element.id} ${element.className}`));

    const metricPattern = /(?:\b\d[\d.,]*\s*[kmb]?\s*(?:\/\s*mo(?:nth)?|searches)?\b|\d+(?:[.,]\d+)?\s*%|[$€£₹¥]\s*\d)/i;
    const rows = [];
    const seenRows = new Set();

    for (const element of scopeElements) {
      const rect = element.getBoundingClientRect();
      if (rect.height > 320 || rect.width < 120) continue;
      const text = normalize(element.innerText || element.textContent || '');
      if (text.length < 3 || text.length > 500 || !metricPattern.test(text)) continue;

      const childTexts = [...element.children]
        .filter(visible)
        .map((child) => normalize(child.innerText || child.textContent || ''))
        .filter(Boolean);
      const leafTexts = [];
      for (const descendant of element.querySelectorAll('*')) {
        if (!visible(descendant)) continue;
        const visibleChildren = [...descendant.children].filter(visible);
        if (visibleChildren.length === 0) {
          const leafText = normalize(descendant.innerText || descendant.textContent || '');
          if (leafText && leafText.length <= 180) leafTexts.push(leafText);
        }
      }

      const texts = [...new Set((childTexts.length >= 2 ? childTexts : leafTexts).filter(Boolean))];
      if (texts.length < 2 || texts.length > 12) continue;
      const key = texts.join('\u241f');
      if (seenRows.has(key)) continue;
      seenRows.add(key);
      rows.push({
        texts,
        tag: element.tagName.toLowerCase(),
        marker: normalize(`${element.id || ''} ${element.className || ''}`).slice(0, 240),
      });
      if (rows.length >= 400) break;
    }

    const supportedCountryNames = [
      'United States', 'United Kingdom', 'India', 'Canada', 'Australia', 'Germany', 'France',
      'Spain', 'Italy', 'Netherlands', 'Brazil', 'Mexico', 'Japan', 'Singapore', 'South Africa',
      'United Arab Emirates', 'New Zealand', 'Ireland', 'Sweden', 'Poland',
    ];
    const rootText = normalize(root?.innerText || root?.textContent || '');
    const mainKeywordMetrics = [
      ...document.querySelectorAll('.ks-main-keyword-widget span.text-gray-base, .surfer-main-keyword-widget span.text-gray-base'),
    ].map((element) => normalize(element.innerText || element.textContent || '')).filter(Boolean);
    const countryLabels = supportedCountryNames.filter((name) => rootText.toLowerCase().includes(name.toLowerCase()));

    const rootSelector = root
      ? `${root.tagName.toLowerCase()}${root.id ? `#${root.id}` : ''}${
          typeof root.className === 'string' && root.className
            ? `.${root.className.trim().split(/\s+/).slice(0, 3).join('.')}`
            : ''
        }`.slice(0, 300)
      : null;

    return {
      rootFound: Boolean(root),
      rootSelector,
      rootText: rootText.slice(0, 20_000),
      markerCount: candidates.length,
      countryLabels,
      mainKeywordMetrics,
      rows,
      frameUrl: location.href,
      capturedAt: new Date().toISOString(),
    };
  }"""

# Injected before every page load in the collector browser. Self-guards against running
# twice and against running in subframes.
FOCUS_MODE_JS = r"""(function focusModeBootstrap() {
  if (window.top !== window || window.__keywordSurferFocusMode) return;
  window.__keywordSurferFocusMode = true;

  const storageKey = 'keyword-surfer-collector-view';
  const allowedAttribute = 'data-ks-focus-allowed';
  const shellAttribute = 'data-ks-focus-shell';
  let refreshTimer = null;

  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = (element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };

  function isChallengePage() {
    const text = normalize(document.body?.innerText).toLowerCase();
    return /\/sorry\//i.test(location.pathname) ||
      text.includes('unusual traffic from your computer network') ||
      text.includes('verify you are human');
  }

  function findPanel() {
    const candidates = [...document.querySelectorAll('div, aside, section')]
      .filter((element) => {
        if (!visible(element)) return false;
        const rect = element.getBoundingClientRect();
        if (rect.width < 320 || rect.width > 680 || rect.height < 220 || rect.height > 1_600) return false;
        const text = normalize(element.innerText || element.textContent);
        return text.includes('Keyword ideas') && text.includes('Volume');
      })
      .map((element) => {
        const text = normalize(element.innerText || element.textContent);
        const rect = element.getBoundingClientRect();
        const score =
          (text.includes('Powered by') ? 30 : 0) +
          (text.includes("What's new") ? 12 : 0) +
          (text.includes('per page') ? 8 : 0) +
          (text.includes('Overlap') ? 6 : 0) +
          (rect.height >= 400 ? 5 : 0) -
          Math.abs(rect.width - 490) / 100;
        return { element, score };
      })
      .sort((left, right) => right.score - left.score);
    return candidates[0]?.element || null;
  }

  function findSearchForm() {
    return document.querySelector('form#tsf') ||
      document.querySelector('textarea[name="q"]')?.closest('form') ||
      document.querySelector('input[name="q"]')?.closest('form') ||
      document.querySelector('form[role="search"]');
  }

  function ensureUi() {
    let style = document.querySelector('#ks-focus-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'ks-focus-style';
      style.textContent = `
        body.ks-focus-mode {
          min-height: 100vh !important;
          margin: 0 !important;
          overflow: auto !important;
          color: #202124 !important;
          background: #f7f7f9 !important;
        }
        body.ks-focus-mode *:not([${allowedAttribute}]) { display: none !important; }
        body.ks-focus-mode [${allowedAttribute}] { box-sizing: border-box; }
        body.ks-focus-mode [${shellAttribute}] {
          display: block !important;
          position: static !important;
          inset: auto !important;
          float: none !important;
          width: 100% !important;
          min-width: 0 !important;
          max-width: none !important;
          height: auto !important;
          min-height: 0 !important;
          max-height: none !important;
          margin: 0 !important;
          padding: 0 !important;
          overflow: visible !important;
          transform: none !important;
          contain: none !important;
        }
        body.ks-focus-mode [data-ks-focus-panel] {
          display: block !important;
          position: relative !important;
          inset: auto !important;
          width: min(520px, calc(100vw - 28px)) !important;
          min-width: 0 !important;
          max-width: 520px !important;
          height: auto !important;
          margin: 18px auto 28px !important;
          transform: none !important;
          filter: none !important;
        }
        body.ks-focus-mode [data-ks-focus-search] {
          display: block !important;
          position: sticky !important;
          z-index: 2147483647 !important;
          top: 14px !important;
          width: min(520px, calc(100vw - 28px)) !important;
          min-width: 0 !important;
          max-width: 520px !important;
          height: 52px !important;
          margin: 14px auto 30px !important;
          padding: 0 !important;
          transform: none !important;
          filter: drop-shadow(0 3px 8px rgba(32, 33, 36, .08)) !important;
        }
        body.ks-focus-mode [data-ks-focus-search] > div,
        body.ks-focus-mode [data-ks-focus-search] .A8SBwf,
        body.ks-focus-mode [data-ks-focus-search] .RNNXgb {
          width: 100% !important;
          min-width: 0 !important;
          max-width: none !important;
        }
        body.ks-focus-mode #ks-focus-controls {
          display: block !important;
          position: fixed !important;
          z-index: 2147483647 !important;
          top: 22px !important;
          right: 18px !important;
          width: 32px !important;
          height: 32px !important;
        }
        body.ks-focus-mode #ks-focus-controls button {
          display: grid !important;
          width: 32px !important;
          height: 32px !important;
          padding: 0 !important;
          place-items: center !important;
          border: 0 !important;
          border-radius: 50% !important;
          cursor: pointer !important;
          color: #4d5156 !important;
          background: #f1f3f4 !important;
          box-shadow: 0 2px 7px rgba(32, 33, 36, .12) !important;
          font: 700 13px/1 Arial, sans-serif !important;
        }
        body.ks-focus-mode #ks-focus-controls button:hover { filter: brightness(.96) !important; }
        #ks-focus-restore {
          display: none;
          position: fixed;
          z-index: 2147483647;
          right: 16px;
          bottom: 16px;
          padding: 10px 14px;
          border: 0;
          border-radius: 999px;
          cursor: pointer;
          color: white;
          background: #6550b9;
          box-shadow: 0 4px 16px rgba(0,0,0,.2);
          font: 600 12px/1.2 Arial, sans-serif;
        }
      `;
      (document.head || document.documentElement).append(style);
    }

    let controls = document.querySelector('#ks-focus-controls');
    if (!controls) {
      controls = document.createElement('div');
      controls.id = 'ks-focus-controls';
      controls.innerHTML = '<button type="button" title="Show full Google page" aria-label="Show full Google page">↗</button>';
      document.body.append(controls);
      controls.querySelector('button').addEventListener('click', () => {
        localStorage.setItem(storageKey, 'full');
        document.body.classList.remove('ks-focus-mode');
        controls.style.display = 'none';
        const restore = document.querySelector('#ks-focus-restore');
        if (restore) restore.style.display = 'block';
      });
    }

    let restore = document.querySelector('#ks-focus-restore');
    if (!restore) {
      restore = document.createElement('button');
      restore.id = 'ks-focus-restore';
      restore.type = 'button';
      restore.textContent = 'Show Surfer only';
      restore.addEventListener('click', () => {
        localStorage.setItem(storageKey, 'focus');
        restore.style.display = 'none';
        scheduleRefresh(0);
      });
      document.body.append(restore);
    }
    return { controls, restore };
  }

  function markTree(element) {
    element.setAttribute(allowedAttribute, '');
    for (const descendant of element.querySelectorAll('*')) descendant.setAttribute(allowedAttribute, '');
    let ancestor = element.parentElement;
    while (ancestor) {
      ancestor.setAttribute(allowedAttribute, '');
      ancestor.setAttribute(shellAttribute, '');
      ancestor = ancestor.parentElement;
    }
  }

  function refresh() {
    if (!document.body) return;
    const { controls, restore } = ensureUi();
    const fullPage = localStorage.getItem(storageKey) === 'full';
    const panel = findPanel();
    const searchForm = findSearchForm();

    if (isChallengePage()) {
      document.body.classList.remove('ks-focus-mode');
      controls.style.display = 'none';
      restore.style.display = 'none';
      return;
    }

    if (fullPage) {
      document.body.classList.remove('ks-focus-mode');
      controls.style.display = 'none';
      restore.style.display = panel ? 'block' : 'none';
      return;
    }

    if (!panel || !searchForm) {
      document.body.classList.remove('ks-focus-mode');
      controls.style.display = 'none';
      restore.style.display = 'none';
      return;
    }

    for (const element of document.querySelectorAll(`[${allowedAttribute}], [${shellAttribute}], [data-ks-focus-panel], [data-ks-focus-search]`)) {
      element.removeAttribute(allowedAttribute);
      element.removeAttribute(shellAttribute);
      element.removeAttribute('data-ks-focus-panel');
      element.removeAttribute('data-ks-focus-search');
    }
    panel.setAttribute('data-ks-focus-panel', '');
    searchForm.setAttribute('data-ks-focus-search', '');
    markTree(panel);
    markTree(searchForm);
    markTree(controls);
    controls.setAttribute(allowedAttribute, '');
    controls.style.display = '';
    restore.style.display = 'none';
    document.body.classList.add('ks-focus-mode');
  }

  function scheduleRefresh(delay = 250) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, delay);
  }

  const start = () => {
    scheduleRefresh(0);
    new MutationObserver(() => scheduleRefresh()).observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})()"""
