"""ingest_kb — pull curated RSS feeds into the platform knowledge base.

Schedule: daily (.github/workflows/ingest_kb.yml).

Each new item goes to llm.summarize_kb(), which returns SKIP unless the item
describes a concrete, actionable platform change. Skipped items are not stored:
the KB is meant to be small and true, since every row competes for space in the
generation prompt. A feed that is 90% marketing fluff is fine — the fluff costs
one cheap LLM call and is dropped.

Dedupe is by url_hash, so an item is summarised at most once ever.

Run:
    python -m src.jobs.ingest_kb --dry-run
    python -m src.jobs.ingest_kb --check-feeds     # no LLM calls, just feed health
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Any

import feedparser

from .. import llm
from ..db import JobRun, RssSource, configure_logging, get_client, insert, iso, load_rss_sources, utcnow

log = logging.getLogger(__name__)

# Per feed, per run. Feeds are polled daily and rarely publish more than a couple
# of items a day; this caps the damage if a feed dumps its whole archive.
MAX_ITEMS_PER_FEED = 15

# Free-tier Gemini is ~15 requests/minute. Space calls out so a multi-feed run
# does not trip the limit and spend the whole job in backoff.
LLM_CALL_SPACING_SECONDS = 4.5

# Ignore items published before this many days ago. A first run against a feed
# with 79 entries would otherwise summarise years of history — expensive, and
# stale platform news is worse than none.
MAX_ITEM_AGE_DAYS = 45

# feedparser has NO timeout of its own and honours the global socket default,
# which is None — i.e. block forever. Observed in practice: a feed that normally
# responds in 0.6s hung for 6 minutes mid-run, then returned 0 entries, which the
# job then reported as a possibly-dead feed. On an Actions runner that silently
# burns the job's whole time budget and misreports a healthy source as broken.
FEED_TIMEOUT_SECONDS = 20


def _set_feed_timeout() -> None:
    """Bound feedparser's network waits.

    Global rather than per-call because feedparser exposes no timeout parameter;
    setting the socket default is the only lever it respects. Safe here: nothing
    else in this job opens a socket that wants to block longer.
    """
    socket.setdefaulttimeout(FEED_TIMEOUT_SECONDS)


def url_hash(url: str) -> str:
    """Stable dedupe key.

    Hashing rather than using the URL directly because feeds are inconsistent
    about trailing slashes and tracking params on otherwise identical links.
    """
    normalised = url.strip().rstrip("/").split("?")[0].lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _entry_published(entry: Any) -> datetime | None:
    """Best-effort publish time from a feedparser entry."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _entry_body(entry: Any) -> str:
    """Longest available text for an entry.

    Feeds vary: some put the full post in content[], some a teaser in summary,
    some only a title. Prefer whichever is longest — more context makes the
    SKIP/keep judgement better.
    """
    candidates: list[str] = []
    for item in entry.get("content") or []:
        value = item.get("value")
        if value:
            candidates.append(value)
    for key in ("summary", "description", "subtitle"):
        value = entry.get(key)
        if value:
            candidates.append(value)
    if not candidates:
        return ""
    best = max(candidates, key=len)
    return _strip_html(best)


def _strip_html(text: str) -> str:
    """Crude tag strip. Feeds ship HTML; the model does not need the markup.

    Deliberately not adding a parser dependency for this — the LLM tolerates
    imperfect whitespace, and the input is truncated anyway.
    """
    import re

    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


def _known_hashes() -> set[str]:
    """All url_hashes already in kb_articles.

    Fetched wholesale: the KB is small by construction (SKIP filters most items),
    so one query beats an IN-list per feed.
    """
    rows = get_client().table("kb_articles").select("url_hash").execute().data or []
    return {r["url_hash"] for r in rows if r["url_hash"]}


def check_feeds() -> int:
    """Report feed health without calling the LLM. Returns count of dead feeds."""
    _set_feed_timeout()
    dead = 0
    for source in load_rss_sources():
        parsed = feedparser.parse(source.url)
        status = getattr(parsed, "status", "?")
        count = len(parsed.entries)
        if count:
            latest = parsed.entries[0].get("title", "")[:50]
            log.info("OK    %-24s http=%s entries=%d | %s", source.name, status, count, latest)
        else:
            dead += 1
            log.error("DEAD  %-24s http=%s entries=0 url=%s", source.name, status, source.url)
    return dead


def _process_feed(job: JobRun, source: RssSource, known: set[str], dry_run: bool) -> None:
    parsed = feedparser.parse(source.url)
    entries = parsed.entries or []
    if not entries:
        # Not fatal, but a permanently dead feed is invisible without this.
        # Distinguish the two causes: a 404 is a rotted URL that needs fixing in
        # config, whereas a timeout on an otherwise-good feed is transient and
        # will likely succeed tomorrow. Reporting both as "dead URL?" sends people
        # chasing a config bug that isn't there.
        status = getattr(parsed, "status", None)
        detail = f"http={status}" if status else "no HTTP response (timeout?)"
        job.note(f"{source.name}: feed returned 0 entries ({detail})")
        job.partial()
        return

    now = utcnow()
    fresh: list[Any] = []
    for entry in entries[:MAX_ITEMS_PER_FEED]:
        link = entry.get("link")
        if not link:
            continue
        digest = url_hash(link)
        if digest in known:
            job.count("skipped_seen")
            continue
        published = _entry_published(entry)
        if published and (now - published).days > MAX_ITEM_AGE_DAYS:
            job.count("skipped_stale")
            continue
        fresh.append(entry)
        known.add(digest)  # guard against duplicate links within one feed

    if not fresh:
        job.note(f"{source.name}: no new items")
        return

    job.note(f"{source.name}: {len(fresh)} new items to triage")
    rows: list[dict] = []

    for i, entry in enumerate(fresh):
        title = entry.get("title", "")
        body = _entry_body(entry)
        link = entry["link"]

        if dry_run:
            log.info("  would triage: %s", title[:70])
            job.count("would_triage")
            continue

        if i:
            time.sleep(LLM_CALL_SPACING_SECONDS)

        try:
            summary, tags = llm.summarize_kb(
                title=title,
                body=body,
                source=source.name,
                # Used only if the model omits the PLATFORMS header. The feed's
                # own tags are coarse — see summarize_kb's docstring.
                fallback_tags=source.platform_tags,
            )
        except llm.LLMError as err:
            log.warning("Summarise failed for %r: %s", title[:50], str(err)[:80])
            job.count("llm_failed")
            job.partial()
            continue

        if summary == llm.SKIP:
            log.info("  SKIP  %s", title[:70])
            job.count("skipped_not_actionable")
            continue

        log.info("  KEEP  [%s] %s", ",".join(tags), title[:60])
        job.count("kept")
        rows.append(
            {
                "source": source.name,
                "url": link,
                "url_hash": url_hash(link),
                "published_at": iso(_entry_published(entry)),
                "platform_tags": tags,
                "summary": summary,
                "version": 1,
                "decay_weight": 1.0,
                "active": True,
                "ingested_at": iso(utcnow()),
            }
        )

    if rows:
        job.count("inserted", insert("kb_articles", rows))


def run(dry_run: bool = False) -> None:
    _set_feed_timeout()
    sources = load_rss_sources()
    if not sources:
        raise SystemExit("config/rss_sources.yaml defines no sources.")

    with JobRun("ingest_kb", dry_run=dry_run) as job:
        known = set() if dry_run else _known_hashes()
        for source in sources:
            try:
                _process_feed(job, source, known, dry_run)
            except Exception as err:  # noqa: BLE001 — one bad feed must not sink the rest
                log.exception("Feed %r failed", source.name)
                job.note(f"feed {source.name!r} failed: {type(err).__name__}: {err}")
                job.partial()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest platform-update RSS feeds into the KB.")
    parser.add_argument("--dry-run", action="store_true", help="Log without writing or calling the LLM.")
    parser.add_argument(
        "--check-feeds",
        action="store_true",
        help="Report which configured feeds are alive, then exit. No LLM calls.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)
    if args.check_feeds:
        dead = check_feeds()
        raise SystemExit(1 if dead else 0)
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
