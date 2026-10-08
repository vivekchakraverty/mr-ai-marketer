"""Link cards must survive Create -> Engage and Create -> Distribute delivery."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.routers import distribution, engage
from app.services import bluesky_delivery, bluesky_link_card, cloud_poster, generation_link, youtube_embed


URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
CARD = bluesky_link_card.Card(URL, "A song", "Rick Astley on YouTube", "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg")


def test_auto_link_detection_is_public_and_trims_sentence_punctuation():
    assert bluesky_link_card.first_url(f"Watch this ({URL}).") == URL
    assert bluesky_link_card.first_url("See http://localhost:8000/private") == ""
    assert bluesky_link_card.first_url("See http://127.0.0.1/private") == ""
    assert bluesky_link_card.first_url("See http://[broken/path") == ""


def test_youtube_card_uses_video_information(monkeypatch):
    monkeypatch.setattr(
        youtube_embed,
        "describe",
        lambda _url: youtube_embed.Video("dQw4w9WgXcQ", URL, "A song", "Rick Astley", CARD.image_url),
    )
    assert bluesky_link_card.describe("https://youtu.be/dQw4w9WgXcQ") == CARD


def test_other_media_link_uses_bluesky_preview_metadata(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "title": "A clip", "description": "A creator's clip",
                "image": "https://cardyb.bsky.app/v1/image?url=preview",
            }

    monkeypatch.setattr(bluesky_link_card.requests, "get", lambda *_args, **_kwargs: Response())
    card = bluesky_link_card.describe("https://vimeo.com/12345678")
    assert card == bluesky_link_card.Card(
        "https://vimeo.com/12345678", "A clip", "A creator's clip",
        "https://cardyb.bsky.app/v1/image?url=preview",
    )


def test_engage_posts_detected_link_as_external_embed(monkeypatch):
    sent = []

    class Client:
        def send_post(self, text, **kwargs):
            sent.append((text, kwargs))
            return SimpleNamespace(uri="at://did:plc:me/app.bsky.feed.post/abc", cid="cid")

    monkeypatch.setattr(engage, "_client", Client)
    monkeypatch.setattr(engage, "_created_feed_post", lambda *_args: None)
    monkeypatch.setattr(bluesky_link_card, "describe", lambda _url: CARD)
    monkeypatch.setattr(bluesky_link_card, "sdk_embed", lambda _client, card: card.record())

    engage.create_post(engage.ComposeRequest(text=f"Watch {URL}"))
    assert sent[0][1]["embed"]["external"]["uri"] == URL
    assert sent[0][1]["embed"]["external"]["title"] == "A song"


def test_distribute_link_uses_native_card_delivery(monkeypatch):
    payload = distribution._payload_for(
        distribution.SendRequest(libraryItemId="item", channels=["bluesky"], text=f"Watch {URL}")
    )
    assert payload["externalUrl"] == URL
    updates = []
    monkeypatch.setattr(distribution.bluesky_delivery, "publish", lambda body: "at://posted" if body == payload else "wrong")
    monkeypatch.setattr(distribution.db, "update_distribution_job", lambda job_id, **fields: updates.append((job_id, fields)))
    monkeypatch.setattr(distribution.activepieces_client, "trigger_webhook", lambda *_: (_ for _ in ()).throw(AssertionError("text-only connector lost the card")))

    distribution.fire_job("job-1", "bluesky", payload)
    assert updates == [("job-1", {"status": "sent", "activepieces_run_id": "at://posted"})]


def test_native_distribute_publishes_the_card_record(monkeypatch):
    sent = []

    class Client:
        def send_post(self, text, **kwargs):
            sent.append((text, kwargs))
            return SimpleNamespace(uri="at://posted")

    monkeypatch.setattr(bluesky_delivery, "_client", Client)
    monkeypatch.setattr(bluesky_link_card, "describe", lambda _url: CARD)
    monkeypatch.setattr(bluesky_link_card, "sdk_embed", lambda _client, card: card.record())
    assert bluesky_delivery.publish({"text": f"Watch {URL}", "externalUrl": URL}) == "at://posted"
    assert sent[0][1]["embed"]["external"]["uri"] == URL


def test_native_post_uri_is_available_to_the_creator_learning_loop(monkeypatch):
    monkeypatch.setattr(
        distribution.activepieces_client,
        "get_flow_run",
        lambda *_args: (_ for _ in ()).throw(AssertionError("an AT URI is already the post reference")),
    )
    assert generation_link._published_uri("at://did:plc:me/app.bsky.feed.post/abc") == "at://did:plc:me/app.bsky.feed.post/abc"


def test_cloud_queue_keeps_card_and_thumbnail(monkeypatch):
    uploads = {}

    class Api:
        def upload_file(self, *, path_in_repo, path_or_fileobj, **_kwargs):
            uploads[path_in_repo] = path_or_fileobj

    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)
    monkeypatch.setattr(cloud_poster, "_api", Api)
    monkeypatch.setattr(cloud_poster, "_setting", lambda _name: "outbox")
    monkeypatch.setattr(cloud_poster, "_attachment", lambda *_args: None)
    monkeypatch.setattr(bluesky_link_card, "describe", lambda _url: CARD)
    monkeypatch.setattr(bluesky_link_card, "thumbnail", lambda _card: (b"image", "image/jpeg"))

    cloud_poster.enqueue("job-1", "bluesky", {"text": f"Watch {URL}", "externalUrl": URL}, "2026-10-08T00:00:00Z")
    record = json.loads(uploads["queue/job-1.json"])
    assert record["externalCard"] == {"uri": URL, "title": "A song", "description": "Rick Astley on YouTube"}
    assert record["mediaFilename"] == "link-card.jpg"
    assert uploads["media/job-1/link-card.jpg"] == b"image"
