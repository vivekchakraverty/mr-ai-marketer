"""Server-side IGDB v4 client: OAuth cache, taxonomy mapping, structural candidates."""
from __future__ import annotations

import os
import re
import threading
import time

import httpx

FIELDS = ("name,slug,summary,storyline,genres.name,themes.name,keywords.name,"
          "game_modes.name,player_perspectives.name,platforms.name,release_dates.date,"
          "similar_games,rating,rating_count,aggregated_rating,aggregated_rating_count,"
          "total_rating,total_rating_count,involved_companies.company.name,"
          "screenshots.image_id,videos.video_id,cover.image_id,websites.url,hypes")
_lock = threading.Lock()
_token = ""
_expiry = 0.0
_last_request = 0.0


class IGDBError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("IGDB_CLIENT_ID") and os.getenv("IGDB_CLIENT_SECRET"))


def _access_token() -> str:
    global _token, _expiry
    with _lock:
        if _token and time.monotonic() < _expiry:
            return _token
        client_id, secret = os.getenv("IGDB_CLIENT_ID"), os.getenv("IGDB_CLIENT_SECRET")
        if not client_id or not secret:
            raise IGDBError("Add IGDB_CLIENT_ID and IGDB_CLIENT_SECRET to enable comparable games.")
        try:
            response = httpx.post("https://id.twitch.tv/oauth2/token",
                                  data={"client_id": client_id, "client_secret": secret,
                                        "grant_type": "client_credentials"}, timeout=15)
            response.raise_for_status()
            value = response.json()
            _token = str(value["access_token"])
            _expiry = time.monotonic() + max(0, int(value.get("expires_in", 3600)) - 60)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise IGDBError("IGDB authentication failed. Check the Twitch client credentials.") from exc
        return _token


def query(endpoint: str, body: str) -> list[dict]:
    global _last_request
    if not re.fullmatch(r"[a-z_]+", endpoint):
        raise ValueError("Invalid IGDB endpoint")
    try:
        with _lock:
            delay = .28 - (time.monotonic() - _last_request)
            if delay > 0:
                time.sleep(delay)
            _last_request = time.monotonic()
        response = httpx.post(f"https://api.igdb.com/v4/{endpoint}", content=body.encode(),
                              headers={"Client-ID": os.environ["IGDB_CLIENT_ID"],
                                       "Authorization": f"Bearer {_access_token()}"}, timeout=20)
        if response.status_code == 429:
            raise IGDBError("IGDB rate limit reached. Retry this analysis later.")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise IGDBError("IGDB returned an unexpected response.")
        return data
    except httpx.HTTPError as exc:
        raise IGDBError(f"IGDB request failed: {exc}") from exc


def taxonomy_terms(endpoint: str, names) -> set[str]:
    """Compare observed genre/perspective wording with IGDB's taxonomy labels."""
    aliases = {
        "genres": {"simulation": "simulator", "rpg": "roleplayingrpg", "roleplaying": "roleplayingrpg"},
        "player_perspectives": {"firstpersonperspective": "firstperson", "thirdpersonperspective": "thirdperson",
                                "birdview": "birdviewisometric", "isometric": "birdviewisometric", "topdown": "birdviewisometric"},
    }
    result = set()
    for name in names:
        term = re.sub(r"[^a-z0-9]", "", str(name).casefold())
        if endpoint == "genres" and term.endswith("shooter"):
            term = "shooter"
        term = aliases.get(endpoint, {}).get(term, term)
        if term:
            result.add(term)
    return result


def _match_ids(endpoint: str, names: list[str]) -> list[int]:
    if not names:
        return []
    # Keywords do not support IGDB's `search` statement. Use a case-insensitive
    # name filter; the small taxonomies fit in one request.
    if endpoint == "keywords":
        rows = []
        for name in names[:3]:
            term = re.sub(r'[^\w -]', '', name)[:60]
            if term:
                rows.extend(query(endpoint, f'fields id,name; where name ~ "{term}"; limit 20;'))
    else:
        rows = query(endpoint, "fields id,name; limit 500;")
    wanted = taxonomy_terms(endpoint, names)
    return [int(row["id"]) for row in rows if taxonomy_terms(endpoint, [row.get("name", "")]) & wanted][:5]


def candidates(profile: dict) -> list[dict]:
    if not configured():
        return []
    concepts = (("genres", profile.get("genres", [])), ("themes", profile.get("themes", [])),
                ("keywords", profile.get("keywords", [])),
                ("player_perspectives", profile.get("player_perspective", [])))
    collected: dict[int, dict] = {}
    for endpoint, names in concepts:
        for concept_id in _match_ids(endpoint, names)[:3]:
            # One query per structural concept makes candidate retrieval robust even when
            # the uploaded game combines tags that no catalogued game shares exactly.
            body = f"fields {FIELDS}; where {endpoint} = {concept_id}; limit 50;"
            for game in query("games", body):
                if game.get("id") and game.get("name"):
                    collected[int(game["id"])] = game
    return list(collected.values())
