"""Explore listener audiences around artist references supplied by the musician.

Essentia's local tempo/key measurements cannot identify fans. This service uses
MusicBrainz artist IDs and ListenBrainz aggregate listening data instead. It
never receives or uploads the song audio, and never returns listener accounts.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

MUSICBRAINZ = "https://musicbrainz.org/ws/2"
LISTENBRAINZ = "https://api.listenbrainz.org/1"
USER_AGENT = "MrAIMarketer/1.0.1 (https://github.com/vivekchakraverty/mr-ai-marketer)"
_musicbrainz_lock = threading.Lock()
_next_musicbrainz_request = 0.0
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class AudienceSourceError(Exception):
    """A public music-data source could not answer the request."""


def _json_request(
    session: requests.Session, method: str, url: str, *, params: dict | None = None,
    body: dict | None = None, musicbrainz: bool = False,
) -> Any:
    global _next_musicbrainz_request
    if musicbrainz:
        # MusicBrainz asks clients to average at most one request per second.
        with _musicbrainz_lock:
            delay = _next_musicbrainz_request - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            _next_musicbrainz_request = time.monotonic() + 1.1
    try:
        response = session.request(
            method, url, params=params, json=body,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=10,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as err:
        logger.info("Music audience source unavailable: %s", err)
        raise AudienceSourceError("A public music-data source is unavailable. Please try again later.") from err


def _artist_match(session: requests.Session, query: str) -> dict | None:
    data = _json_request(
        session, "GET", f"{MUSICBRAINZ}/artist/",
        params={"query": query, "fmt": "json", "limit": 5}, musicbrainz=True,
    )
    if not isinstance(data, dict):
        return None
    candidates = data.get("artists")
    if not isinstance(candidates, list):
        return None
    exact = [artist for artist in candidates
             if isinstance(artist, dict)
             and str(artist.get("name", "")).casefold() == query.casefold()
             and isinstance(artist.get("id"), str)
             and _MBID.fullmatch(artist["id"])]
    if not exact:
        return None
    return max(exact, key=lambda artist: int(artist.get("score") or 0))


def _nearby_artists(session: requests.Session, mbid: str) -> list[dict]:
    data = _json_request(
        session, "GET", f"{LISTENBRAINZ}/lb-radio/artist/{mbid}",
        params={"mode": "easy", "max_similar_artists": 8,
                "max_recordings_per_artist": 1, "pop_begin": 0, "pop_end": 100},
    )
    if not isinstance(data, dict):
        return []
    artists = []
    for artist_id, recordings in data.items():
        if artist_id == mbid or not isinstance(recordings, list):
            continue
        for recording in recordings:
            if not isinstance(recording, dict):
                continue
            name = recording.get("similar_artist_name")
            similar_id = recording.get("similar_artist_mbid")
            if isinstance(name, str) and name.strip() and isinstance(similar_id, str) and _MBID.fullmatch(similar_id):
                artists.append({"name": name.strip(), "mbid": similar_id})
                break
    return artists


def explore_music_audience(reference_artists: list[str], *, session: requests.Session | None = None) -> dict:
    """Return documented audience leads, never a listener-likelihood score."""
    if not 1 <= len(reference_artists) <= 3:
        raise ValueError("Enter one to three reference artists.")
    own_session = session is None
    session = session or requests.Session()
    try:
        matched = []
        unmatched = []
        for name in reference_artists:
            artist = _artist_match(session, name)
            if artist is None:
                unmatched.append(name)
                continue
            matched.append({
                "query": name,
                "name": artist["name"],
                "mbid": artist["id"],
                "musicbrainz_url": f"https://musicbrainz.org/artist/{artist['id']}",
                "unique_listeners": None,
                "total_listens": None,
            })

        warnings = []
        if matched:
            try:
                popularity = _json_request(
                    session, "POST", f"{LISTENBRAINZ}/popularity/artist",
                    body={"artist_mbids": [item["mbid"] for item in matched]},
                )
                if isinstance(popularity, list):
                    counts = {item.get("artist_mbid"): item for item in popularity if isinstance(item, dict)}
                    for artist in matched:
                        entry = counts.get(artist["mbid"], {})
                        for source, target in (("total_user_count", "unique_listeners"),
                                               ("total_listen_count", "total_listens")):
                            value = entry.get(source)
                            if isinstance(value, int) and value >= 0:
                                artist[target] = value
            except AudienceSourceError:
                warnings.append("ListenBrainz listener counts are temporarily unavailable.")

        nearby: dict[str, dict] = {}
        for seed in matched:
            try:
                suggestions = _nearby_artists(session, seed["mbid"])
            except AudienceSourceError:
                warnings.append(f"Related artists for {seed['name']} are temporarily unavailable.")
                continue
            for artist in suggestions:
                item = nearby.setdefault(artist["mbid"], {
                    **artist,
                    "musicbrainz_url": f"https://musicbrainz.org/artist/{artist['mbid']}",
                    "reference_artists": [],
                })
                if seed["name"] not in item["reference_artists"]:
                    item["reference_artists"].append(seed["name"])

        adjacent = sorted(nearby.values(), key=lambda artist: (-len(artist["reference_artists"]), artist["name"].casefold()))[:8]
        return {
            "reference_artists": matched,
            "adjacent_artists": adjacent,
            "unmatched": unmatched,
            "warnings": warnings,
        }
    finally:
        if own_session:
            session.close()
