"""Turn a raw Surfer panel snapshot into keyword rows.

A direct port of the pure half of the standalone collector's surfer-extractor.js. The DOM
half stays in JavaScript (see keyword_surfer_js.CAPTURE_JS) because it has to run inside
the page; everything below operates on the snapshot that comes back, so it lives here where
it can be tested without a browser.

The parsing is deliberately shape-driven rather than selector-driven: it reads whatever
visible text the extension rendered and works out which token is a keyword, which is a
volume, which is a price. Surfer ships UI changes regularly, and a parser pinned to its
class names is a parser that breaks on their next release. Newer Surfer versions omit CPC
from rendered idea rows; that missing field is restored from the record Surfer has already
loaded into its browser cache, while the DOM remains authoritative for row membership.
"""
from __future__ import annotations

import re
import math
from datetime import datetime, timezone

from .keyword_surfer_js import HEADER_TERMS

LABEL_TERMS = {
    "keyword",
    "keywords",
    "keyword ideas",
    "search volume",
    "volume",
    "similarity",
    "cpc",
    "content ideas",
    "export",
    "clipboard",
    "collection",
    "select all",
}

_VOLUME_WORDS = re.compile(r"(?:searches|monthly|per month|/\s*mo(?:nth)?)", re.IGNORECASE)
_COMPACT = re.compile(r"^(?:[~≈]\s*)?([0-9][0-9.,\s]*)([kmb])?$", re.IGNORECASE)
_PERCENT = re.compile(r"^([0-9]+(?:[.,][0-9]+)?)\s*%$")
_CURRENCY = re.compile(r"^([^\d\s.,-]{1,4}|[A-Z]{3}\s*)?\s*([0-9]+(?:[.,][0-9]+)?)\s*([A-Z]{3})?$")
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_THOUSANDS_DOT = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_ACTION_WORDS = re.compile(r"^(add|open|close|copy|save|remove|show|hide|view|load more)$", re.IGNORECASE)
_PANEL_HINT = re.compile(r"keyword ideas|search volume|similar keywords", re.IGNORECASE)

_MULTIPLIER = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def normalize_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace(" ", " ")).strip()


def normalize_keyword(value) -> str:
    return normalize_text(value).casefold()


def parse_compact_number(value) -> int | None:
    """"1.2K/mo" -> 1200. None when the token is not a plain count.

    Percentages and prices must fall through: they are numbers too, and mistaking a 72%
    similarity for a 72/month volume produces a row that looks plausible and is wrong.
    """
    text = _VOLUME_WORDS.sub("", normalize_text(value)).strip()
    match = _COMPACT.match(text)
    if not match:
        return None

    numeric = re.sub(r"\s", "", match.group(1))
    suffix = (match.group(2) or "").lower()

    if suffix:
        numeric = numeric.replace(",", "")
    elif _THOUSANDS_COMMA.match(numeric):
        numeric = numeric.replace(",", "")
    elif _THOUSANDS_DOT.match(numeric):
        # European grouping: 1.234 is one thousand two hundred, not 1.234.
        numeric = numeric.replace(".", "")
    else:
        numeric = numeric.replace(",", "")

    try:
        parsed = float(numeric)
    except ValueError:
        return None
    return round(parsed * _MULTIPLIER.get(suffix, 1))


def parse_percentage(value) -> float | None:
    match = _PERCENT.match(normalize_text(value))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def parse_currency(value) -> dict | None:
    """A price only when a symbol or ISO code is present — a bare number is a volume."""
    text = normalize_text(value)
    match = _CURRENCY.match(text)
    if not match or (not match.group(1) and not match.group(3)):
        return None
    try:
        amount = float(match.group(2).replace(",", "."))
    except ValueError:
        return None
    return {
        "amount": amount,
        "currency": normalize_text(match.group(1) or match.group(3)),
        "display": text,
    }


def _is_metric_token(value) -> bool:
    text = normalize_text(value)
    return parse_percentage(text) is not None or parse_currency(text) is not None or parse_compact_number(text) is not None


def is_keyword_candidate(value) -> bool:
    text = normalize_text(value)
    if len(text) < 2 or len(text) > 160:
        return False
    lowered = text.casefold()
    if lowered in LABEL_TERMS or lowered in HEADER_TERMS:
        return False
    if _is_metric_token(text):
        return False
    if _ACTION_WORDS.match(text):
        return False
    # JS used /[\p{L}\p{N}]/u; Python's re has no \p, and str.isalnum is the same test
    # per character including non-Latin scripts.
    return any(ch.isalnum() for ch in text)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def parse_row(row: dict) -> dict | None:
    texts = [t for t in _unique([normalize_text(t) for t in (row.get("texts") or [])]) if t]
    if len(texts) < 2:
        return None

    keyword = next((t for t in texts if is_keyword_candidate(t)), None)
    if not keyword:
        return None

    similarity = next((v for v in (parse_percentage(t) for t in texts) if v is not None), None)
    currency = next((v for v in (parse_currency(t) for t in texts) if v is not None), None)

    explicit_volume = next(
        (t for t in texts if _VOLUME_WORDS.search(t) and parse_compact_number(t) is not None), None
    )
    bare_numbers = [
        parsed
        for parsed in (
            parse_compact_number(t)
            for t in texts
            if parse_percentage(t) is None and parse_currency(t) is None
        )
        if parsed is not None
    ]
    volume = parse_compact_number(explicit_volume) if explicit_volume else (bare_numbers[0] if bare_numbers else None)

    if volume is None and similarity is None and currency is None:
        return None

    return {
        "keyword": keyword,
        "volume": volume,
        "cpc": currency["amount"] if currency else None,
        "cpcDisplay": currency["display"] if currency else None,
        "similarity": similarity,
    }


def _parse_cached_metric(metric: dict) -> dict | None:
    """Normalize one metric from Keyword Surfer's already-loaded browser cache.

    Surfer 6.3 still loads CPC for each idea but no longer renders that column. Cache data
    only fills fields absent from the rendered row; it never decides which rows exist.
    """
    keyword = normalize_text(metric.get("keyword"))
    if not keyword:
        return None

    volume = parse_compact_number(metric.get("volume"))
    raw_cpc = metric.get("cpc")
    cpc = None
    display = None
    if raw_cpc not in (None, "") and not isinstance(raw_cpc, bool):
        currency = parse_currency(raw_cpc)
        if currency:
            cpc = currency["amount"]
            display = currency["display"]
        else:
            try:
                candidate = float(str(raw_cpc).replace(",", ""))
                if math.isfinite(candidate) and candidate >= 0:
                    cpc = candidate
                    display = f"${candidate:.2f}"
            except (TypeError, ValueError):
                pass

    if volume is None and cpc is None:
        return None
    return {
        "keyword": keyword,
        "volume": volume,
        "cpc": cpc,
        "cpcDisplay": display,
    }


def _metric_following_label(root_text: str, labels: list[str]) -> int | None:
    escaped = "|".join(re.escape(label) for label in labels)
    expression = re.compile(
        rf"(?:{escaped})\s*[:–—-]?\s*([0-9][0-9.,\s]*[kmb]?(?:\s*/\s*mo(?:nth)?)?)", re.IGNORECASE
    )
    match = expression.search(root_text)
    return parse_compact_number(match.group(1)) if match else None


def parse_snapshot(snapshot: dict, query: str) -> dict:
    """The panel snapshot as one result: the query's own figures plus its suggestions."""
    query_key = normalize_keyword(query)
    parsed_rows = [r for r in (parse_row(row) for row in (snapshot.get("rows") or [])) if r]
    cached_rows = [
        r
        for r in (_parse_cached_metric(metric) for metric in (snapshot.get("cachedKeywordMetrics") or []))
        if r
    ]
    cached_by_keyword = {normalize_keyword(row["keyword"]): row for row in cached_rows}

    # The extension's rendered row remains authoritative. Its cache only restores metrics
    # that the current UI omitted, most notably CPC in Keyword Surfer 6.3.
    for row in parsed_rows:
        cached = cached_by_keyword.get(normalize_keyword(row["keyword"]))
        if not cached:
            continue
        if row["volume"] is None:
            row["volume"] = cached["volume"]
        if row["cpc"] is None:
            row["cpc"] = cached["cpc"]
            row["cpcDisplay"] = cached["cpcDisplay"]

    by_keyword: dict[str, dict] = {}
    for row in parsed_rows:
        key = normalize_keyword(row["keyword"])
        previous = by_keyword.get(key)
        if previous is None:
            by_keyword[key] = row
            continue
        # First sighting wins per field; later rows only fill what is still missing.
        by_keyword[key] = {
            **previous,
            "volume": previous["volume"] if previous["volume"] is not None else row["volume"],
            "cpc": previous["cpc"] if previous["cpc"] is not None else row["cpc"],
            "cpcDisplay": previous["cpcDisplay"] or row["cpcDisplay"],
            "similarity": previous["similarity"] if previous["similarity"] is not None else row["similarity"],
        }

    exact = by_keyword.pop(query_key, None)
    cached_exact = cached_by_keyword.get(query_key)

    root_text = normalize_text(snapshot.get("rootText") or "")
    inline_metrics = [t for t in (normalize_text(m) for m in (snapshot.get("mainKeywordMetrics") or [])) if t]
    inline_volume = parse_compact_number(inline_metrics[0]) if inline_metrics else None
    inline_cpc = None
    if len(inline_metrics) > 1:
        try:
            inline_cpc = float(inline_metrics[1].replace(",", ""))
        except ValueError:
            inline_cpc = None

    query_volume = (exact or {}).get("volume")
    if query_volume is None:
        query_volume = (cached_exact or {}).get("volume")
    if query_volume is None:
        query_volume = inline_volume
    if query_volume is None:
        query_volume = _metric_following_label(root_text, ["search volume", "volume"])

    suggestions = sorted(
        (r for r in by_keyword.values() if normalize_keyword(r["keyword"]) != query_key),
        key=lambda r: r["volume"] if r["volume"] is not None else -1,
        reverse=True,
    )

    country_labels = snapshot.get("countryLabels") or []
    loaded = bool(
        snapshot.get("rootFound")
        and (query_volume is not None or suggestions or _PANEL_HINT.search(root_text))
    )

    cpc = (exact or {}).get("cpc")
    if cpc is None:
        cpc = (cached_exact or {}).get("cpc")
    if cpc is None:
        cpc = inline_cpc
    cpc_display = (exact or {}).get("cpcDisplay")
    if cpc_display is None:
        cpc_display = (cached_exact or {}).get("cpcDisplay")
    if cpc_display is None and inline_cpc is not None:
        cpc_display = inline_metrics[1]

    return {
        "source": "keyword-surfer-rendered-ui",
        "loaded": loaded,
        "query": query,
        "volume": query_volume,
        "cpc": cpc,
        "cpcDisplay": cpc_display,
        "countryLabel": country_labels[0] if country_labels else None,
        "suggestions": suggestions,
        "diagnostics": {
            "rootFound": bool(snapshot.get("rootFound")),
            "rootSelector": snapshot.get("rootSelector"),
            "markerCount": snapshot.get("markerCount") or 0,
            "candidateRows": len(snapshot.get("rows") or []),
            "parsedRows": len(parsed_rows),
            "cachedMetrics": len(cached_rows),
            "capturedAt": snapshot.get("capturedAt") or datetime.now(timezone.utc).isoformat(),
            "frameUrls": snapshot.get("frameUrls") or ([snapshot["frameUrl"]] if snapshot.get("frameUrl") else []),
            "mainKeywordMetrics": inline_metrics,
            "rootText": root_text[:12_000],
        },
    }
