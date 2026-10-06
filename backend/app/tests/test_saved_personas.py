"""Shared readers and research boundaries after removing the persona wizard."""
from __future__ import annotations

import json

import pytest
import requests
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import config, db
from app.routers import marketing_plan, saved_personas as saved_router
from app.services import public_research, saved_personas


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "saved-personas.sqlite3")
    db.init_db()
    document = {"personas": [{"id": "p1", "label": "Planning buyers", "summary": "Compare before buying",
                              "confidence": "Low"}], "answers": {"A1": "Planning app"}}
    with db._connect() as conn:
        for run_id, status in (("saved", "complete"), ("unfinished", "draft")):
            conn.execute("INSERT INTO persona_runs VALUES (?,?,?,?,?,?,?)",
                         (run_id, "Saved research", 5, status, json.dumps(document), "2026-10-06", "2026-10-06"))


def test_saved_choices_remain_read_only_and_exclude_unfinished_runs(database):
    app = FastAPI()
    app.include_router(saved_router.router)
    with TestClient(app) as client:
        options = client.get("/personas/options").json()["options"]
        assert [(item["runId"], item["personaId"]) for item in options] == [("saved", "p1")]
        assert client.post("/personas/runs", json={"name": "New run"}).status_code == 404
        assert client.get("/personas/questions").status_code == 404
        assert client.get("/personas/active").status_code == 404
    assert saved_personas.get_run("saved")["answers"]["A1"] == "Planning app"
    assert saved_personas.get_run("missing") is None
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM persona_runs").fetchone()[0] == 2


def test_marketing_plan_still_uses_a_saved_target(database, monkeypatch):
    monkeypatch.setattr(marketing_plan.marketing_plan_space, "is_configured", lambda: True)
    captured = []
    sentinel = object()
    monkeypatch.setattr(marketing_plan, "_generate_via_space", lambda body, _industry: captured.append(body) or sentinel)
    body = marketing_plan.GeneratePlanRequest(productDescription="A planning app", hfToken="synthetic-test-token",
                                             targetPersonaRunId="saved", targetPersonaId="p1")
    assert marketing_plan.generate_plan(body) is sentinel
    assert "Planning buyers" in captured[0].productDescription
    assert "directional research" in captured[0].productDescription
    assert body.productDescription == "A planning app"


@pytest.mark.parametrize("run_id, status", [("missing", 404), ("unfinished", 400)])
def test_marketing_plan_rejects_unavailable_saved_targets(database, run_id, status):
    body = marketing_plan.GeneratePlanRequest(productDescription="A planning app", hfToken="synthetic-test-token",
                                             targetPersonaRunId=run_id, targetPersonaId="p1")
    with pytest.raises(HTTPException) as error:
        marketing_plan.generate_plan(body)
    assert error.value.status_code == status


def test_reused_feedback_still_scrubs_pii_and_credentials_are_not_answer_labels():
    value = saved_personas.scrub("My name is Alice Smith. Email alice@example.com or call +1 555 123 4567; https://example.com and @alice.")
    assert "Alice" not in value and "alice@" not in value and "555" not in value
    assert "example.com" not in value and "@alice" not in value
    assert {"E1", "E2", "E3", "E5"}.isdisjoint(saved_personas.ANSWER_LABELS)
    assert saved_personas.answer_lines("- first; • second\nthird") == ["first", "second", "third"]


class Session:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.replies)


def response(status, text=""):
    value = requests.Response()
    value.status_code = status
    value._content = text.encode()
    return value


@pytest.fixture
def public_reads(monkeypatch):
    monkeypatch.setattr(public_research, "_cache", {})
    monkeypatch.setattr(public_research, "_last_request", __import__("collections").defaultdict(float))
    monkeypatch.setattr(public_research.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(public_research, "_public_host", lambda _host: True)


def test_public_research_honors_robots_before_fetching_a_page(public_reads):
    session = Session(response(200, "User-agent: *\nDisallow: /private"))
    with pytest.raises(PermissionError):
        public_research.get("https://example.org/private", robots=True, session=session)
    assert [url for url, _kwargs in session.calls] == ["https://example.org/robots.txt"]


def test_public_research_does_not_follow_redirects(public_reads):
    session = Session(response(404), response(301))
    with pytest.raises(ValueError, match="Redirected sources"):
        public_research.get("https://example.org/article", robots=True, session=session)
    assert all(kwargs["allow_redirects"] is False for _url, kwargs in session.calls)


def test_public_research_reuses_cached_response(public_reads):
    session = Session(response(200, "Public research"))
    first = public_research.get("https://example.org/article", session=session)
    assert public_research.get("https://example.org/article", session=session) is first
    assert len(session.calls) == 1
    assert session.calls[0][1]["headers"]["User-Agent"] == public_research.USER_AGENT


def test_public_research_refuses_private_hosts_without_sending_a_request(monkeypatch):
    monkeypatch.setattr(public_research.socket, "getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))])
    session = Session()
    with pytest.raises(ValueError, match="public HTTPS"):
        public_research.get("https://private.example/article", session=session)
    assert not session.calls
