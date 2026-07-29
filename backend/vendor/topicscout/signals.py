"""Historical measurement collectors — current window vs preceding equal window.

Ported from TrendScout's signals.py. Discovery feeds nominate candidates; nothing
here treats a feed mention as proof of acceleration. Each collector measures one
quantity over two adjacent equal windows and converts the change into a bounded
within-source score, so sources with wildly different units stay comparable.

The only change from upstream is where the snapshot database lives: DATA_DIR
(per-OS-user, set by Electron) rather than next to the source, which is read-only
in a packaged build.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

USER_AGENT = "TopicScout/1.0 (measurable trend research)"
TIMEOUT = 12


@dataclass
class SignalMeasurement:
    topic: str
    source: str
    family: str
    current: float
    baseline: float
    unit: str
    score: float | None
    change_pct: float | None
    sample_size: int
    note: str
    url: str
    context_only: bool = False


def _get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = TIMEOUT) -> dict:
    if params:
        url = f"{url}?{urlencode(params)}"
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    with urlopen(Request(url, headers=request_headers), timeout=timeout) as response:
        return json.loads(response.read())


def _periods(days: int, maximum: int = 90) -> tuple[date, date, date, date]:
    window = min(max(days, 7), maximum)
    current_end = datetime.now(timezone.utc).date()
    current_start = current_end - timedelta(days=window - 1)
    baseline_end = current_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=window - 1)
    return baseline_start, baseline_end, current_start, current_end


def _score(current: float, baseline: float, sample_size: int) -> float:
    if current <= 0:
        return 0.0
    scale = max(current, baseline, 1.0)
    epsilon = max(0.01, scale * 0.03)
    log_ratio = math.log((current + epsilon) / (baseline + epsilon))
    reliability = min(16.0, 4.0 * math.log1p(max(0, sample_size)))
    return round(max(0.0, min(100.0, 50.0 + 32.0 * math.tanh(log_ratio) + reliability)), 1)


def _measurement(
    *,
    topic: str,
    source: str,
    family: str,
    current: float,
    baseline: float,
    unit: str,
    sample_size: int,
    note: str,
    url: str,
    context_only: bool = False,
) -> SignalMeasurement:
    change_pct = None if baseline <= 0 else round((current - baseline) / baseline * 100, 1)
    return SignalMeasurement(
        topic=topic,
        source=source,
        family=family,
        current=round(float(current), 4),
        baseline=round(float(baseline), 4),
        unit=unit,
        score=None if context_only else _score(current, baseline, sample_size),
        change_pct=change_pct,
        sample_size=sample_size,
        note=note,
        url=url,
        context_only=context_only,
    )


def _parse_gdelt_date(value: str) -> datetime:
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def measure_gdelt(topic: str, days: int, config: dict) -> SignalMeasurement:
    baseline_start, _, current_start, current_end = _periods(days, maximum=45)
    data = _get_json(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        {
            "query": f'"{topic}"',
            "mode": "timelinevol",
            "format": "json",
            "startdatetime": baseline_start.strftime("%Y%m%d000000"),
            "enddatetime": current_end.strftime("%Y%m%d235959"),
        },
        timeout=25,
    )
    points: list[tuple[datetime, float]] = []
    for series in data.get("timeline", []):
        for point in series.get("data", []):
            try:
                points.append((_parse_gdelt_date(str(point["date"])), float(point["value"])))
            except (KeyError, TypeError, ValueError):
                continue
    cutoff = datetime.combine(current_start, datetime.min.time(), tzinfo=timezone.utc)
    current_values = [value for stamp, value in points if stamp >= cutoff]
    baseline_values = [value for stamp, value in points if stamp < cutoff]
    current = sum(current_values) / max(1, len(current_values))
    baseline = sum(baseline_values) / max(1, len(baseline_values))
    return _measurement(
        topic=topic,
        source="GDELT TimelineVol",
        family="news",
        current=current,
        baseline=baseline,
        unit="% of monitored coverage",
        sample_size=len(current_values),
        note="Average normalized share of global monitored news versus the preceding equal period.",
        url=(
            "https://api.gdeltproject.org/api/v2/doc/doc?"
            + urlencode({"query": f'"{topic}"', "mode": "timelinevol", "format": "html"})
        ),
    )


def measure_wikimedia(topic: str, days: int, config: dict) -> SignalMeasurement:
    search = _get_json(
        "https://en.wikipedia.org/w/api.php",
        {"action": "query", "list": "search", "srsearch": topic, "srlimit": 1, "format": "json"},
    )
    results = search.get("query", {}).get("search", [])
    if not results:
        raise ValueError("no matching Wikipedia article")
    title = results[0]["title"]
    baseline_start, _, current_start, current_end = _periods(days)
    data = _get_json(
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia.org/all-access/user/{quote(title.replace(' ', '_'), safe='')}/daily/"
        f"{baseline_start:%Y%m%d}/{current_end:%Y%m%d}"
    )
    current = 0.0
    baseline = 0.0
    current_points = 0
    for item in data.get("items", []):
        stamp = datetime.strptime(str(item["timestamp"])[:8], "%Y%m%d").date()
        if stamp >= current_start:
            current += float(item.get("views", 0))
            current_points += 1
        else:
            baseline += float(item.get("views", 0))
    return _measurement(
        topic=topic,
        source="Wikimedia pageviews",
        family="attention",
        current=current,
        baseline=baseline,
        unit="article views",
        sample_size=current_points,
        note=f'English Wikipedia article matched to "{title}"; current total versus the preceding equal period.',
        url=f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
    )


def _algolia_count(topic: str, start: date, end: date) -> int:
    start_ts = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()) - 1
    data = _get_json(
        "https://hn.algolia.com/api/v1/search_by_date",
        {
            "query": topic,
            "tags": "story",
            "numericFilters": f"created_at_i>={start_ts},created_at_i<={end_ts}",
            "hitsPerPage": 0,
        },
    )
    return int(data.get("nbHits", 0))


def measure_hacker_news(topic: str, days: int, config: dict) -> SignalMeasurement:
    baseline_start, baseline_end, current_start, current_end = _periods(days)
    current = _algolia_count(topic, current_start, current_end)
    baseline = _algolia_count(topic, baseline_start, baseline_end)
    return _measurement(
        topic=topic,
        source="Hacker News velocity",
        family="conversation",
        current=current,
        baseline=baseline,
        unit="matching stories",
        sample_size=current,
        note="Matching Hacker News stories versus the preceding equal period.",
        url=f"https://hn.algolia.com/?q={quote(topic)}",
    )


def _reddit_count(topic: str, sort: str, window: str) -> int:
    """Reddit exposes no count endpoint without auth, so listing length is the proxy."""
    import time  # noqa: PLC0415 — only this collector backs off
    import xml.etree.ElementTree as ET  # noqa: PLC0415 — only this collector parses XML
    from urllib.error import HTTPError  # noqa: PLC0415

    url = (
        f"https://old.reddit.com/search.rss?q={quote(topic)}"
        f"&sort={sort}&t={window}&type=link&limit=100"
    )
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/atom+xml,application/xml,text/xml;q=0.9",
    })
    for attempt in range(2):
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                root = ET.fromstring(response.read())
            return len(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
        except HTTPError as err:
            # Anonymous readers get throttled hard but briefly. One backoff, then give
            # up and let the caller record it as a source outage.
            if err.code != 429 or attempt:
                raise
            time.sleep(8)
    return 0


def measure_reddit(topic: str, days: int, config: dict) -> SignalMeasurement:
    """Reddit conversation volume, this window versus the next window out.

    Reddit's key-free search can only filter to preset windows (week/month/year),
    not arbitrary date ranges, so the baseline here is the next widest bucket rather
    than a true preceding equal period. The measurement is honest about that in its
    note, and it is capped at 100 results per bucket by Reddit itself.
    """
    current_window, baseline_window = ("week", "month") if days <= 14 else ("month", "year")
    current = _reddit_count(topic, "new", current_window)
    # A wider bucket contains the narrower one, so the comparable baseline is the
    # per-window average of the remainder, not the raw wider count.
    wider = _reddit_count(topic, "new", baseline_window)
    ratio = 4.35 if current_window == "week" else 12.0
    baseline = max(0.0, (wider - current) / max(1.0, ratio - 1))
    return _measurement(
        topic=topic,
        source="Reddit conversation",
        family="conversation",
        current=float(current),
        baseline=baseline,
        unit="matching posts",
        sample_size=current,
        note=(
            f"Matching Reddit posts in the last {current_window} versus the average "
            f"{current_window} across the last {baseline_window}. Reddit's key-free search caps "
            "each bucket at 100 results, so very large topics saturate."
        ),
        url=f"https://old.reddit.com/search?q={quote(topic)}",
    )


def _github_count(topic: str, start: date, end: date, token: str) -> int:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = _get_json(
        "https://api.github.com/search/repositories",
        {"q": f'"{topic}" created:{start.isoformat()}..{end.isoformat()}', "per_page": 1},
        headers,
    )
    return int(data.get("total_count", 0))


def measure_github(topic: str, days: int, config: dict) -> SignalMeasurement:
    baseline_start, baseline_end, current_start, current_end = _periods(days, maximum=60)
    token = str(config.get("github_token") or os.getenv("GITHUB_TOKEN", ""))
    current = _github_count(topic, current_start, current_end, token)
    baseline = _github_count(topic, baseline_start, baseline_end, token)
    return _measurement(
        topic=topic,
        source="GitHub repository growth",
        family="adoption",
        current=current,
        baseline=baseline,
        unit="new repositories",
        sample_size=current,
        note="New public repositories matching the topic versus the preceding equal period.",
        url=f"https://github.com/search?q={quote(topic)}&type=repositories",
    )


def _openalex_count(topic: str, start: date, end: date, email: str) -> int:
    params = {
        "search": topic,
        "filter": f"from_publication_date:{start.isoformat()},to_publication_date:{end.isoformat()}",
        "per-page": 1,
    }
    if email:
        params["mailto"] = email
    data = _get_json("https://api.openalex.org/works", params)
    return int(data.get("meta", {}).get("count", 0))


def measure_openalex(topic: str, days: int, config: dict) -> SignalMeasurement:
    baseline_start, baseline_end, current_start, current_end = _periods(days, maximum=90)
    email = str(config.get("contact_email") or "")
    current = _openalex_count(topic, current_start, current_end, email)
    baseline = _openalex_count(topic, baseline_start, baseline_end, email)
    return _measurement(
        topic=topic,
        source="OpenAlex publications",
        family="research",
        current=current,
        baseline=baseline,
        unit="indexed works",
        sample_size=current,
        note="Indexed scholarly works by publication date versus the preceding equal period.",
        url=f"https://openalex.org/works?search={quote(topic)}",
    )


def _pubmed_count(topic: str, start: date, end: date, email: str) -> int:
    params = {
        "db": "pubmed",
        "term": topic,
        "datetype": "pdat",
        "mindate": start.strftime("%Y/%m/%d"),
        "maxdate": end.strftime("%Y/%m/%d"),
        "retmax": 0,
        "retmode": "json",
        "tool": "topic_scout",
    }
    if email:
        params["email"] = email
    data = _get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params)
    return int(data.get("esearchresult", {}).get("count", 0))


def measure_pubmed(topic: str, days: int, config: dict) -> SignalMeasurement:
    baseline_start, baseline_end, current_start, current_end = _periods(days, maximum=90)
    email = str(config.get("contact_email") or "")
    current = _pubmed_count(topic, current_start, current_end, email)
    baseline = _pubmed_count(topic, baseline_start, baseline_end, email)
    return _measurement(
        topic=topic,
        source="PubMed publications",
        family="research",
        current=current,
        baseline=baseline,
        unit="indexed citations",
        sample_size=current,
        note="PubMed citations by publication date versus the preceding equal period.",
        url=f"https://pubmed.ncbi.nlm.nih.gov/?term={quote(topic)}",
    )


def _sec_count(topic: str, start: date, end: date, user_agent: str) -> int:
    data = _get_json(
        "https://efts.sec.gov/LATEST/search-index",
        {
            "q": f'"{topic}"',
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
            "from": 0,
            "size": 1,
        },
        {"User-Agent": user_agent},
    )
    total = data.get("hits", {}).get("total", 0)
    return int(total.get("value", 0) if isinstance(total, dict) else total)


def measure_sec(topic: str, days: int, config: dict) -> SignalMeasurement:
    contact_email = str(config.get("contact_email") or "").strip()
    if "@" not in contact_email:
        raise ValueError("contact email required by SEC access policy")
    user_agent = f"TopicScout trend research {contact_email}"
    baseline_start, baseline_end, current_start, current_end = _periods(days, maximum=90)
    current = _sec_count(topic, current_start, current_end, user_agent)
    baseline = _sec_count(topic, baseline_start, baseline_end, user_agent)
    return _measurement(
        topic=topic,
        source="SEC filing mentions",
        family="adoption",
        current=current,
        baseline=baseline,
        unit="matching filings",
        sample_size=current,
        note="Full-text U.S. filing mentions versus the preceding equal period.",
        url=f"https://www.sec.gov/edgar/search/#/q={quote(topic)}",
    )


def _reliefweb_count(topic: str, start: date, end: date, appname: str) -> int:
    data = _get_json(
        "https://api.reliefweb.int/v2/reports",
        {
            "appname": appname,
            "query[value]": topic,
            "query[operator]": "AND",
            "filter[field]": "date.created",
            "filter[value][from]": f"{start.isoformat()}T00:00:00+00:00",
            "filter[value][to]": f"{end.isoformat()}T23:59:59+00:00",
            "limit": 0,
        },
    )
    return int(data.get("totalCount", 0))


def measure_reliefweb(topic: str, days: int, config: dict) -> SignalMeasurement:
    appname = str(config.get("reliefweb_appname") or "").strip()
    if not appname:
        raise ValueError("pre-approved ReliefWeb appname required")
    baseline_start, baseline_end, current_start, current_end = _periods(days, maximum=90)
    current = _reliefweb_count(topic, current_start, current_end, appname)
    baseline = _reliefweb_count(topic, baseline_start, baseline_end, appname)
    return _measurement(
        topic=topic,
        source="ReliefWeb reports",
        family="adoption",
        current=current,
        baseline=baseline,
        unit="humanitarian reports",
        sample_size=current,
        note="Matching ReliefWeb reports versus the preceding equal period.",
        url=f"https://reliefweb.int/search/results?search={quote(topic)}",
    )


def measure_fred(topic: str, days: int, config: dict) -> SignalMeasurement:
    api_key = str(config.get("fred_api_key") or "").strip()
    if not api_key:
        raise ValueError("FRED API key required")
    search = _get_json(
        "https://api.stlouisfed.org/fred/series/search",
        {"search_text": topic, "api_key": api_key, "file_type": "json", "limit": 1, "order_by": "search_rank"},
    )
    series = search.get("seriess", [])
    if not series:
        raise ValueError("no relevant FRED series")
    selected = series[0]
    series_id = selected["id"]
    baseline_start, _, current_start, current_end = _periods(max(days, 180), maximum=365)
    data = _get_json(
        "https://api.stlouisfed.org/fred/series/observations",
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": baseline_start.isoformat(),
            "observation_end": current_end.isoformat(),
        },
    )
    current_values: list[float] = []
    baseline_values: list[float] = []
    for item in data.get("observations", []):
        try:
            value = float(item["value"])
            stamp = date.fromisoformat(item["date"])
        except (KeyError, TypeError, ValueError):
            continue
        (current_values if stamp >= current_start else baseline_values).append(value)
    if not current_values or not baseline_values:
        raise ValueError("insufficient observations in matched FRED series")
    current = sum(current_values) / len(current_values)
    baseline = sum(baseline_values) / len(baseline_values)
    return _measurement(
        topic=topic,
        source=f"FRED · {series_id}",
        family="context",
        current=current,
        baseline=baseline,
        unit=str(selected.get("units") or "series units"),
        sample_size=len(current_values),
        note=(
            f'Context only: "{selected.get("title", series_id)}". Direction is not treated '
            "as topic momentum because economic series require analyst interpretation."
        ),
        url=f"https://fred.stlouisfed.org/series/{series_id}",
        context_only=True,
    )


SIGNAL_FETCHERS: dict[str, Callable[[str, int, dict], SignalMeasurement]] = {
    "GDELT TimelineVol": measure_gdelt,
    "Wikimedia pageviews": measure_wikimedia,
    "Hacker News velocity": measure_hacker_news,
    "Reddit conversation": measure_reddit,
    "GitHub repository growth": measure_github,
    "OpenAlex publications": measure_openalex,
    "PubMed publications": measure_pubmed,
    "SEC filing mentions": measure_sec,
    "ReliefWeb reports": measure_reliefweb,
    "FRED economic context": measure_fred,
}

# Unauthenticated search quotas make repository and filing searches unsuitable for an
# unbounded topic fan-out. The cap is explicit rather than silently allowing rate
# limits to distort lower-ranked topics.
SOURCE_TOPIC_CAPS = {
    "GitHub repository growth": 5,
    "SEC filing mentions": 8,
    "FRED economic context": 5,
    "Reddit conversation": 6,
}


def _history_path(config: dict) -> Path:
    configured = str(config.get("history_path") or os.getenv("TOPIC_SCOUT_HISTORY", "")).strip()
    if configured:
        return Path(configured)
    data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent))
    return data_dir / "topic-scout-history.sqlite3"


def _record_history(measurements: list[SignalMeasurement], config: dict) -> None:
    if not measurements:
        return
    path = _history_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_history (
                    captured_at TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    source TEXT NOT NULL,
                    family TEXT NOT NULL,
                    current_value REAL NOT NULL,
                    baseline_value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    score REAL,
                    PRIMARY KEY (captured_at, topic, source)
                )
                """
            )
            captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            connection.executemany(
                """
                INSERT OR REPLACE INTO signal_history
                (captured_at, topic, source, family, current_value, baseline_value, unit, score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        captured_at, item.topic, item.source, item.family,
                        item.current, item.baseline, item.unit, item.score,
                    )
                    for item in measurements
                ],
            )
    except (OSError, sqlite3.Error):
        # Read-only deployments still receive live, source-provided baselines.
        pass


def measure_topics(
    topics: list[str],
    days: int,
    sources: list[str],
    config: dict | None = None,
) -> tuple[dict[str, list[SignalMeasurement]], list[str]]:
    config = config or {}
    results: dict[str, list[SignalMeasurement]] = {topic: [] for topic in topics}
    errors: list[str] = []
    jobs: dict = {}
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(topics) * max(1, len(sources))))) as pool:
        for source in sources:
            fetcher = SIGNAL_FETCHERS.get(source)
            if fetcher is None:
                continue
            cap = SOURCE_TOPIC_CAPS.get(source, len(topics))
            for topic in topics[:cap]:
                jobs[pool.submit(fetcher, topic, days, config)] = (topic, source)
        for future in as_completed(jobs):
            topic, source = jobs[future]
            try:
                results[topic].append(future.result())
            except Exception as exc:  # noqa: BLE001 — an outage is a source-health note
                errors.append(f"{source} · {topic}: {type(exc).__name__}: {exc}")
    measurements = [item for topic_items in results.values() for item in topic_items]
    _record_history(measurements, config)
    return results, errors
