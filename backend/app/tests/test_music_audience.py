"""Audience research must stay evidence-backed when public APIs vary or fail."""

from __future__ import annotations

import requests
import pytest
from fastapi import HTTPException

from app.routers import music_audience as audience_router
from app.services import music_audience


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.data


class Session:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.replies)


def test_uses_exact_artist_ids_and_aggregate_listener_counts():
    seed_id = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    related_id = "cc197bad-dc9c-440d-a5b5-d52ba2e14234"
    session = Session([
        Response({"artists": [
            {"name": "Radiohead Tribute", "id": "wrong", "score": 100},
            {"name": "Radiohead", "id": seed_id, "score": 95},
        ]}),
        Response([{"artist_mbid": seed_id, "total_user_count": 123, "total_listen_count": 456}]),
        Response({
            seed_id: [{"similar_artist_name": "Radiohead", "similar_artist_mbid": seed_id}],
            related_id: [{"similar_artist_name": "Coldplay", "similar_artist_mbid": related_id}],
        }),
    ])

    result = music_audience.explore_music_audience(["Radiohead"], session=session)

    assert result["reference_artists"][0]["mbid"] == seed_id
    assert result["reference_artists"][0]["unique_listeners"] == 123
    assert result["adjacent_artists"] == [{
        "name": "Coldplay", "mbid": related_id,
        "musicbrainz_url": f"https://musicbrainz.org/artist/{related_id}",
        "reference_artists": ["Radiohead"],
    }]
    assert all("User-Agent" in call[2]["headers"] for call in session.calls)
    assert all("audio" not in str(call) for call in session.calls)


def test_missing_match_and_listenbrainz_failure_do_not_invent_an_audience():
    seed_id = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    session = Session([
        Response({"artists": [{"name": "Someone else", "id": "wrong", "score": 99}]}),
        Response({"artists": [{"name": "Radiohead", "id": seed_id, "score": 95}]}),
        Response({}, status=429),
        Response({}, status=429),
    ])
    result = music_audience.explore_music_audience(["Unknown Artist", "Radiohead"], session=session)

    assert result["unmatched"] == ["Unknown Artist"]
    assert result["reference_artists"][0]["unique_listeners"] is None
    assert result["adjacent_artists"] == []
    assert len(result["warnings"]) == 2


def test_route_limits_reference_artists_before_calling_external_sources(monkeypatch):
    called = False

    def explore(_names):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(audience_router, "explore_music_audience", explore)
    with pytest.raises(HTTPException) as error:
        audience_router.audience(audience_router.AudienceRequest(
            reference_artists="One, Two, Three, Four"
        ))
    assert error.value.status_code == 400
    assert not called
