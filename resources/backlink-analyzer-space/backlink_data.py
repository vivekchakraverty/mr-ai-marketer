"""Normalisation and WAT parsing primitives shared by ingestion and the Space."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import tldextract
from warcio.archiveiterator import ArchiveIterator


# Never download the Public Suffix List at application runtime. This keeps the
# Space deterministic and works in networking-restricted environments.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=None)
_SPACE_RE = re.compile(r"\s+")


def normalized_host(value: str) -> str:
    """Return a lower-case host without a leading www., or an empty string."""
    candidate = (value or "").strip().lower().rstrip(".")
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate


def url_host(value: str) -> str:
    """Safely extract and normalise a hostname from an absolute URL."""
    try:
        return normalized_host(urlsplit(value).hostname or "")
    except (TypeError, ValueError):
        return ""


def registered_domain(value: str) -> str:
    """Get an eTLD+1 domain, falling back to the normalised host for intranets."""
    host = normalized_host(value)
    if not host:
        return ""
    parts = _EXTRACT(host)
    if parts.domain and parts.suffix:
        return f"{parts.domain}.{parts.suffix}"
    return host


def canonical_target_domain(value: str) -> str:
    """Accept a URL or hostname and return the domain used as a partition key."""
    candidate = (value or "").strip()
    if not candidate:
        return ""
    host = url_host(candidate)
    if not host:
        host = normalized_host(candidate.split("/")[0])
    return registered_domain(host)


def normalize_link_url(value: str) -> str:
    """Normalise HTTP(S) URLs while retaining a useful, user-visible path/query."""
    try:
        parts = urlsplit(value)
    except (TypeError, ValueError):
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    host = normalized_host(parts.hostname)
    if not host:
        return ""
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), f"{host}{port}", path, parts.query, ""))


def clean_text(value: Any, limit: int = 500) -> str:
    """Keep anchor text compact enough for a dashboard and safe for Parquet."""
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    return text[:limit]


def is_nofollow(rel: str) -> bool:
    return "nofollow" in {item.lower() for item in clean_text(rel).split()}


def wat_links(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Return WAT HTML link dictionaries without assuming optional fields exist."""
    try:
        return (
            payload["Envelope"]["Payload-Metadata"]["HTTP-Response-Metadata"]
            ["HTML-Metadata"].get("Links", [])
        )
    except (KeyError, TypeError):
        return []


def extract_backlinks_from_payload(
    *,
    payload: dict[str, Any],
    source_url: str,
    target_domain: str,
    crawl: str,
    wat_source: str,
    capture_date: str = "",
) -> list[dict[str, str]]:
    """Extract destination-domain backlinks from one Common Crawl WAT payload."""
    source_url = normalize_link_url(source_url)
    source_host = url_host(source_url)
    target_domain = canonical_target_domain(target_domain)
    if not source_url or not source_host or not target_domain:
        return []

    rows: list[dict[str, str]] = []
    for link in wat_links(payload):
        if not isinstance(link, dict):
            continue
        raw_url = link.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        target_url = normalize_link_url(urljoin(source_url, raw_url))
        target_host = url_host(target_url)
        if not target_url or not target_host:
            continue
        if target_host != target_domain and not target_host.endswith(f".{target_domain}"):
            continue
        rel = clean_text(link.get("rel", ""))
        link_type = clean_text(link.get("path", link.get("type", "")))
        rows.append(
            {
                "source_url": source_url,
                "source_host": source_host,
                "source_domain": registered_domain(source_host),
                "target_url": target_url,
                "target_host": target_host,
                "target_domain": target_domain,
                "anchor_text": clean_text(link.get("text", "")),
                "rel": rel,
                "link_type": link_type,
                "is_nofollow": str(is_nofollow(rel)).lower(),
                "capture_date": capture_date,
                "crawl": clean_text(crawl, 100),
                "wat_source": wat_source,
            }
        )
    return rows


def extract_backlinks_from_wat(
    stream: Any,
    *,
    target_domain: str,
    crawl: str,
    wat_source: str,
    max_records: int | None = None,
) -> list[dict[str, str]]:
    """Parse a gzipped WAT stream and return backlinks to one destination domain."""
    rows: list[dict[str, str]] = []
    metadata_records = 0
    for record in ArchiveIterator(stream):
        if record.rec_type != "metadata":
            continue
        metadata_records += 1
        if max_records is not None and metadata_records > max_records:
            break
        source_url = record.rec_headers.get_header("WARC-Target-URI") or ""
        capture_date = record.rec_headers.get_header("WARC-Date") or ""
        try:
            raw_payload = record.content_stream().read()
            payload = json.loads(raw_payload.decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError, TypeError):
            continue
        rows.extend(
            extract_backlinks_from_payload(
                payload=payload,
                source_url=source_url,
                target_domain=target_domain,
                crawl=crawl,
                wat_source=wat_source,
                capture_date=capture_date,
            )
        )
    return rows


def manifest_template(target_domain: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "target_domain": canonical_target_domain(target_domain),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "partitions": [],
    }
