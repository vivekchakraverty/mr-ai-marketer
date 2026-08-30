"""Tracing a published post back to the draft that wrote it.

The Social Post learning loop only measures generations carrying a `posted_uri`, and
nothing ever set one — 94 generations, none linked, an hourly snapshot job with an empty
set to work on. These cover the edge that closes that gap, and in particular the cases
where it must NOT link: a draft nobody sent, a send that has not landed, and a run that
reports no post.
"""

from __future__ import annotations

import sys
import types

import pytest

from app import db
from app.services import generation_link


@pytest.fixture()
def spg(monkeypatch):
    """Stand in for the vendored package, which pulls torch in for real."""
    calls: list[dict] = []

    def attach_posted_uri(*, generation_id: int, posted_uri: str, niche: str) -> str:
        calls.append({"generation_id": generation_id, "uri": posted_uri, "niche": niche})
        return posted_uri

    generation = types.ModuleType("vendor.socialpost.src.generation")
    generation.attach_posted_uri = attach_posted_uri
    src = types.ModuleType("vendor.socialpost.src")
    src.generation = generation
    for name, module in (
        ("vendor", types.ModuleType("vendor")),
        ("vendor.socialpost", types.ModuleType("vendor.socialpost")),
        ("vendor.socialpost.src", src),
        ("vendor.socialpost.src.generation", generation),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return calls


@pytest.fixture()
def run_output(monkeypatch):
    """The automation run's reported output, per run id."""
    from app.services import activepieces_client

    runs: dict[str, dict] = {}

    def get_flow_run(run_id: str) -> dict:
        # An unknown run is how a purged or unreachable one behaves, and the sweep is
        # expected to treat that as "try again later" rather than an error.
        if run_id not in runs:
            raise activepieces_client.ActivepiecesError(f"no run {run_id}")
        return runs[run_id]

    monkeypatch.setattr(activepieces_client, "get_flow_run", get_flow_run)
    return runs


def _sent_draft(uri_run_id: str, *, status: str = "sent", channel: str = "bluesky") -> str:
    """A generated draft, filed in the Library, whose distribution job reached `status`."""
    item = db.add_item(tool="Social", title="draft", subtitle="bluesky · personal", content="words")
    db.record_generation_link(item["id"], 7, "bluesky", "personal")
    job = db.add_distribution_job(item["id"], channel, "sending", payload="{}")
    db.update_distribution_job(job["id"], status=status, activepieces_run_id=uri_run_id)
    return item["id"]


def _run_with(uri: str) -> dict:
    return {"steps": {"post_to_bluesky": {"output": {"mainPost": {"uri": uri}}}}}


def test_a_published_post_is_linked_to_its_draft(app_db, spg, run_output):
    item_id = _sent_draft("run-1")
    run_output["run-1"] = _run_with("at://did:plc:abc/app.bsky.feed.post/xyz")

    assert generation_link.link_sent_bluesky_posts() == 1
    assert spg == [
        {"generation_id": 7, "uri": "at://did:plc:abc/app.bsky.feed.post/xyz", "niche": "personal"}
    ]
    assert db.unlinked_generation_links("bluesky") == []
    # The local record is what stops the next sweep doing it all again.
    assert not any(r["library_item_id"] == item_id for r in db.unlinked_generation_links("bluesky"))


def test_linking_is_not_repeated_on_the_next_sweep(app_db, spg, run_output):
    _sent_draft("run-1")
    run_output["run-1"] = _run_with("at://did:plc:abc/app.bsky.feed.post/xyz")

    assert generation_link.link_sent_bluesky_posts() == 1
    assert generation_link.link_sent_bluesky_posts() == 0
    assert len(spg) == 1


def test_a_draft_nobody_sent_is_not_linked(app_db, spg, run_output):
    """A generation with no distribution job has no published post to point at, and must
    not be re-examined on every tick for the life of the install."""
    item = db.add_item(tool="Social", title="draft", subtitle="bluesky · personal", content="words")
    db.record_generation_link(item["id"], 7, "bluesky", "personal")

    assert generation_link.link_sent_bluesky_posts() == 0
    assert spg == []


@pytest.mark.parametrize("status", ["scheduled", "sending", "failed", "cancelled"])
def test_only_a_send_that_landed_counts(app_db, spg, run_output, status):
    _sent_draft("run-1", status=status)
    run_output["run-1"] = _run_with("at://did:plc:abc/app.bsky.feed.post/xyz")

    assert generation_link.link_sent_bluesky_posts() == 0
    assert spg == []


def test_a_run_reporting_no_post_is_left_for_a_later_sweep(app_db, spg, run_output):
    """Not an error and not a permanent skip: the run may simply not have been readable
    yet, and the row stays pending so the next tick can try again."""
    _sent_draft("run-1")
    run_output["run-1"] = {"steps": {"post_to_bluesky": {"output": {}}}}

    assert generation_link.link_sent_bluesky_posts() == 0
    assert spg == []
    assert len(db.unlinked_generation_links("bluesky")) == 1


def test_another_channels_send_is_not_mistaken_for_a_bluesky_post(app_db, spg, run_output):
    _sent_draft("run-1", channel="mastodon")
    run_output["run-1"] = _run_with("at://did:plc:abc/app.bsky.feed.post/xyz")

    assert generation_link.link_sent_bluesky_posts() == 0
    assert spg == []


def test_regenerating_into_the_same_entry_keeps_the_newest_draft(app_db, spg):
    """The draft that will be published is the last one written, so the link follows it."""
    item = db.add_item(tool="Social", title="draft", subtitle="bluesky · personal", content="w")
    db.record_generation_link(item["id"], 7, "bluesky", "personal")
    db.record_generation_link(item["id"], 9, "bluesky", "personal")

    db.add_distribution_job(item["id"], "bluesky", "sent", payload="{}")
    rows = db.unlinked_generation_links("bluesky")
    assert [r["generation_id"] for r in rows] == [9]
