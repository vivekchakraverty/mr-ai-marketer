"""Calendar geography, movable dates, and complete timezone-aware schedule coverage."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import distribution_calendar as calendar
from app.routers import distribution


def test_calendar_is_exactly_thirty_days_and_crosses_year_end():
    result = calendar.events(date(2026, 12, 20), "US", "")
    assert result["start"] == "2026-12-20"
    assert result["end"] == "2027-01-18"
    assert all(result["start"] <= row["date"] <= result["end"] for row in result["events"])
    assert any(row["date"] == "2027-01-01" and "New Year" in row["name"] for row in result["events"])


def test_movable_observances_use_requested_year_not_scraped_display_date():
    rows = calendar.international_events(2026)
    assert (date(2026, 10, 5), "World Habitat Day") in rows
    assert (date(2026, 11, 19), "World Philosophy Day") in rows
    assert (date(2026, 11, 15), "World Day of Remembrance for Road Traffic Victims") in rows
    week = [day for day, name in rows if name == "World Space Week"]
    assert week == [date(2026, 10, day) for day in range(4, 11)]


def test_country_is_chosen_by_user_and_region_does_not_leak():
    start = date(2026, 10, 20)
    international = calendar.events(start)
    assert all(row["scope"] == "international" for row in international["events"])
    karnataka = calendar.events(start, "IN", "KA")
    assert any(row["scope"] == "regional" and row["date"] == "2026-11-01"
               and any("Karnataka" in region for region in row["regions"]) for row in karnataka["events"])
    assert all(row["country"] in (None, "IN") for row in karnataka["events"])
    assert all(row["regions"] == [] or all("Karnataka" in name for name in row["regions"])
               for row in karnataka["events"])
    national = calendar.events(start, "IN", "")
    assert not any(row["scope"] == "regional" for row in national["events"])
    assert any("Diwali" in row["name"] for row in national["events"])


def test_all_regions_are_grouped_and_national_events_not_repeated():
    rows = calendar.events(date(2026, 10, 20), "IN", "all")["events"]
    keys = [(row["date"], row["name"], row["scope"], row["country"]) for row in rows]
    assert len(keys) == len(set(keys))
    assert any(row["scope"] == "regional" for row in rows)
    assert all(row["source"].startswith("https://") for row in rows)
    assert "WB" in dict(calendar.regions("IN"))
    assert "West Bengal" == dict(calendar.regions("IN"))["WB"]


def test_worldwide_covers_multiple_countries_and_unofficial_celebrations():
    rows = calendar.events(date(2026, 10, 20), "worldwide")["events"]
    assert {"US", "IN", "CA"} <= {row["country"] for row in rows}
    assert any("Halloween" in row["name"] for row in rows)


@pytest.mark.parametrize("country,region", [("XX", "all"), ("IN", "CA")])
def test_bad_geography_is_rejected(country, region):
    with pytest.raises(distribution.HTTPException) as error:
        distribution.calendar_events(date(2026, 10, 6), country, region)
    assert error.value.status_code == 400


def test_calendar_schedules_are_not_hidden_by_history_limit_and_use_instant_boundaries(app_db):
    start = datetime(2026, 10, 6, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    end = start + timedelta(days=30)
    local = app_db.add_distribution_job("item", "bluesky", "scheduled", scheduled_at=start.isoformat())
    cloud = app_db.add_distribution_job("item", "mastodon", "scheduled_cloud",
        scheduled_at=(end - timedelta(seconds=1)).astimezone(timezone.utc).isoformat())
    app_db.add_distribution_job("item", "bluesky", "scheduled", scheduled_at=end.isoformat())
    app_db.add_distribution_job("item", "bluesky", "scheduled", scheduled_at=(start - timedelta(seconds=1)).isoformat())
    app_db.add_distribution_job("item", "bluesky", "cancelled", scheduled_at=start.isoformat())
    app_db.add_distribution_job("item", "bluesky", "sent", scheduled_at=start.isoformat())
    with app_db._connect() as conn:
        conn.execute("UPDATE distribution_jobs SET created_at='2000-01-01' WHERE id IN (?,?)", (local["id"], cloud["id"]))
    for _ in range(110):
        app_db.add_distribution_job("item", "bluesky", "sent")
    assert local["id"] not in {row["id"] for row in app_db.list_distribution_jobs()}
    assert {row["id"] for row in distribution.calendar_posts(start, end)["jobs"]} == {local["id"], cloud["id"]}
    app_db.cancel_scheduled_distribution_job(local["id"])
    assert [row["id"] for row in distribution.calendar_posts(start, end)["jobs"]] == [cloud["id"]]


def test_calendar_posts_rejects_ambiguous_or_unbounded_ranges():
    with pytest.raises(distribution.HTTPException):
        distribution.calendar_posts(datetime(2026, 10, 6), datetime(2026, 11, 5))
    start = datetime(2026, 10, 6, tzinfo=timezone.utc)
    with pytest.raises(distribution.HTTPException):
        distribution.calendar_posts(start, start + timedelta(days=60))


def test_calendar_http_contract_parses_local_offsets_and_validates_location(app_db):
    app = FastAPI()
    app.include_router(distribution.router)
    client = TestClient(app)
    locations = client.get("/distribution/calendar/locations", params={"country": "IN"})
    assert locations.status_code == 200
    assert "IN" in locations.json()["countries"]
    assert {"code": "WB", "name": "West Bengal"} in locations.json()["regions"]
    response = client.get("/distribution/calendar/events", params={"start": "2026-10-06", "country": "IN", "region": "WB"})
    assert response.status_code == 200
    assert response.json()["end"] == "2026-11-04"
    assert client.get("/distribution/calendar/events", params={"start": "2026-10-06", "country": "IN", "region": "CA"}).status_code == 400
    assert client.get("/distribution/calendar/events", params={"start": "not-a-date"}).status_code == 422
    scheduled = app_db.add_distribution_job("item", "bluesky", "scheduled", scheduled_at="2026-10-05T19:00:00+00:00")
    posts = client.get("/distribution/calendar/posts", params={"start": "2026-10-06T00:00:00+05:30", "end": "2026-11-05T00:00:00+05:30"})
    assert posts.status_code == 200
    assert [row["id"] for row in posts.json()["jobs"]] == [scheduled["id"]]
