"""Regression coverage for media crossing the Distribute -> Activepieces boundary.

The job history stores the webhook body, while the social network only sees the inputs
bound by the published flow.  An upgraded install once kept its already-ENABLED,
text-only flow forever, which made those two views disagree: history showed an image and
Bluesky received none.
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from app import config
from app.routers import distribution
from app.services import (
    activepieces_client,
    image_prompt,
    mastodon_delivery,
    share_server,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BLUESKY_TEMPLATE = _REPO_ROOT / "resources" / "activepieces" / "flows" / "bluesky.json"
_MASTODON_TEMPLATE = (
    _REPO_ROOT / "resources" / "activepieces" / "flows" / "mastodon.json"
)


def _one_template(tmp_path: Path) -> tuple[Path, dict]:
    spec = json.loads(_BLUESKY_TEMPLATE.read_text(encoding="utf-8"))
    flows = tmp_path / "flows"
    flows.mkdir()
    (flows / "bluesky.json").write_text(json.dumps(spec), encoding="utf-8")
    return flows, spec


def test_enabled_old_flow_is_reimported_and_published(tmp_path, monkeypatch):
    flows, spec = _one_template(tmp_path)
    old_trigger = copy.deepcopy(spec["trigger"])
    old_trigger["nextAction"]["settings"]["pieceVersion"] = "0.1.5"
    old_trigger["nextAction"]["settings"]["input"].pop("imageUrls")
    existing = {
        "id": "flow-bluesky",
        "status": "ENABLED",
        "version": {"displayName": spec["displayName"], "trigger": old_trigger},
    }
    imported: list[tuple[dict, str | None]] = []
    published: list[str] = []

    monkeypatch.setattr(activepieces_client, "_FLOWS_DIR", flows)
    monkeypatch.setattr(activepieces_client, "list_flows", lambda: [existing])

    def fake_import(candidate: dict, flow_id: str | None = None) -> dict:
        imported.append((candidate, flow_id))
        return {"id": flow_id or "new-flow"}

    monkeypatch.setattr(activepieces_client, "_import_flow", fake_import)
    monkeypatch.setattr(activepieces_client, "_publish_flow", published.append)

    assert activepieces_client.ensure_flows_imported() == {"bluesky": "flow-bluesky"}
    assert imported == [(spec, "flow-bluesky")]
    assert published == ["flow-bluesky"]


def test_matching_enabled_flow_is_a_no_op_after_expression_rewrite(
    tmp_path, monkeypatch
):
    flows, spec = _one_template(tmp_path)
    persisted_trigger = copy.deepcopy(spec["trigger"])

    def persisted(value):
        if isinstance(value, dict):
            return {key: persisted(item) for key, item in value.items()}
        if isinstance(value, list):
            return [persisted(item) for item in value]
        if isinstance(value, str):
            return value.replace("{{trigger.body.", "{{trigger['output'].body.")
        return value

    existing = {
        "id": "flow-bluesky",
        "status": "ENABLED",
        "version": {
            "displayName": spec["displayName"],
            "trigger": persisted(persisted_trigger),
            "serverAddedMetadata": True,
        },
    }

    monkeypatch.setattr(activepieces_client, "_FLOWS_DIR", flows)
    monkeypatch.setattr(activepieces_client, "list_flows", lambda: [existing])
    monkeypatch.setattr(
        activepieces_client,
        "_import_flow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("matching flow was reimported")
        ),
    )
    monkeypatch.setattr(
        activepieces_client,
        "_publish_flow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("matching flow was republished")
        ),
    )

    assert activepieces_client.ensure_flows_imported() == {"bluesky": "flow-bluesky"}


def test_stale_enabled_unrelated_flow_is_not_incidentally_migrated(tmp_path, monkeypatch):
    spec = json.loads(_BLUESKY_TEMPLATE.read_text(encoding="utf-8"))
    spec["displayName"] = "Discord broadcast"
    flows = tmp_path / "flows"
    flows.mkdir()
    (flows / "discord.json").write_text(json.dumps(spec), encoding="utf-8")
    stale = copy.deepcopy(spec["trigger"])
    stale["nextAction"]["settings"]["input"] = {"text": "old input"}
    existing = {
        "id": "flow-discord",
        "status": "ENABLED",
        "version": {"displayName": spec["displayName"], "trigger": stale},
    }
    monkeypatch.setattr(activepieces_client, "_FLOWS_DIR", flows)
    monkeypatch.setattr(activepieces_client, "list_flows", lambda: [existing])
    monkeypatch.setattr(
        activepieces_client,
        "_import_flow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unrelated enabled flow was reimported")
        ),
    )

    assert activepieces_client.ensure_flows_imported() == {"discord": "flow-discord"}


def test_social_templates_bind_the_connector_specific_media_shapes():
    spec = json.loads(_BLUESKY_TEMPLATE.read_text(encoding="utf-8"))
    action_input = spec["trigger"]["nextAction"]["settings"]["input"]
    assert action_input["imageUrls"] == "{{trigger.body.imageUrls}}"
    assert action_input["videoUrl"] == "{{trigger.body.videoUrl}}"
    assert action_input["videoAltText"] == "{{trigger.body.videoFileAlt}}"

    mastodon = json.loads(_MASTODON_TEMPLATE.read_text(encoding="utf-8"))
    mastodon_input = mastodon["trigger"]["nextAction"]["settings"]["input"]
    # Mastodon's action declares a single Activepieces File property, whose dynamic
    # value is a fetchable URL. It is deliberately scalar, unlike Bluesky's image array.
    assert mastodon_input["media"] == "{{trigger.body.mediaUrl}}"


def test_fire_job_materializes_media_at_the_webhook_boundary(monkeypatch):
    delivered: list[tuple[str, dict]] = []
    shared: list[str] = []

    def share(url: str) -> str:
        shared.append(url)
        return "http://engine-host/shared/signed-image.png"

    monkeypatch.setattr(distribution, "_shareable_media_url", share)
    monkeypatch.setattr(
        distribution.activepieces_client,
        "trigger_webhook",
        lambda channel, payload: delivered.append((channel, payload)),
    )
    monkeypatch.setattr(
        distribution.activepieces_client, "get_flow_id", lambda _channel: "flow-bluesky"
    )
    monkeypatch.setattr(
        distribution.activepieces_client,
        "list_flow_runs_since",
        lambda *_args, **_kwargs: [
            {
                "id": "run-1",
                "status": "SUCCEEDED",
                "steps": {
                    "trigger": {
                        "output": {
                            "body": {
                                distribution._DELIVERY_JOB_FIELD: "job-1"
                            }
                        }
                    }
                },
            }
        ],
    )
    monkeypatch.setattr(distribution.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(distribution, "_record_run_outcome", lambda *_args: None)

    canonical = {
        "text": "hello",
        "imageUrl": "/outputs/mastodon/run/post-image.png",
        "mediaUrl": "/outputs/mastodon/run/post-image.png",
        "imageUrls": ["/outputs/mastodon/run/post-image.png"],
    }
    distribution.fire_job("job-1", "bluesky", canonical)

    assert shared == [
        "/outputs/mastodon/run/post-image.png",
        "/outputs/mastodon/run/post-image.png",
        "/outputs/mastodon/run/post-image.png",
    ]
    assert delivered == [
        (
            "bluesky",
            {
                **canonical,
                "imageUrl": "http://engine-host/shared/signed-image.png",
                "mediaUrl": "http://engine-host/shared/signed-image.png",
                "imageUrls": ["http://engine-host/shared/signed-image.png"],
                distribution._DELIVERY_JOB_FIELD: "job-1",
            },
        )
    ]


def test_fire_job_fails_instead_of_sending_unfetchable_media(monkeypatch):
    updates: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        distribution,
        "_materialize_media_payload",
        lambda _payload: (_ for _ in ()).throw(
            distribution.HTTPException(status_code=400, detail="attachment is gone")
        ),
    )
    monkeypatch.setattr(
        distribution.activepieces_client,
        "trigger_webhook",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("unfetchable media reached Activepieces")
        ),
    )
    monkeypatch.setattr(
        distribution.db,
        "update_distribution_job",
        lambda job_id, **fields: updates.append((job_id, fields)),
    )

    distribution.fire_job(
        "job-1",
        "mastodon",
        {"text": "hello", "mediaUrl": "/outputs/missing.png"},
    )

    assert updates == [
        ("job-1", {"status": "failed", "error": "attachment is gone"})
    ]


def test_distribution_payload_carries_scalar_and_bluesky_array_shapes():
    body = distribution.SendRequest(
        libraryItemId="library-item",
        channels=["bluesky"],
        text="hello",
        imageUrl="https://images.example/post.png",
    )

    payload = distribution._payload_for(body)

    assert payload["imageUrl"] == "https://images.example/post.png"
    assert payload["imageUrls"] == ["https://images.example/post.png"]


def test_bluesky_gets_prepared_media_while_other_channels_keep_original(monkeypatch):
    monkeypatch.setattr(
        image_prompt,
        "prepare_bluesky_image",
        lambda _url: "/outputs/.distribution-media/prepared.bluesky.webp",
    )
    monkeypatch.setattr(
        distribution,
        "_shareable_media_url",
        lambda url, _scheduled=None: f"shared:{url}",
    )
    body = distribution.SendRequest(
        libraryItemId="library-item",
        channels=["bluesky", "mastodon"],
        text="hello",
        imageUrl="/outputs/social/run/post-image.png",
    )

    canonical = distribution._payload_for(body)
    payload = distribution._materialize_media_payload(canonical)

    assert payload["imageUrl"] == "shared:/outputs/social/run/post-image.png"
    assert payload["imageUrls"] == [
        "shared:/outputs/.distribution-media/prepared.bluesky.webp"
    ]


def test_immediate_send_keeps_canonical_history_and_fires_fetchable_links(monkeypatch):
    monkeypatch.setattr(distribution.db, "list_custom_channels", lambda: [])
    monkeypatch.setattr(
        image_prompt,
        "prepare_bluesky_image",
        lambda _url: "/outputs/.distribution-media/prepared.bluesky.webp",
    )
    monkeypatch.setattr(
        distribution,
        "_shareable_media_url",
        lambda url, _scheduled=None: f"signed:{url}",
    )
    stored: list[dict] = []
    fired: list[tuple[str, str, dict]] = []

    def add_job(library_item_id, channel, status, **fields):
        stored.append({"library_item_id": library_item_id, "channel": channel, "status": status, **fields})
        return {"id": "job-1", "channel": channel, "status": status}

    monkeypatch.setattr(distribution.db, "add_distribution_job", add_job)
    monkeypatch.setattr(
        distribution.db,
        "get_distribution_job",
        lambda job_id: {"id": job_id, "channel": "bluesky", "status": "sent"},
    )
    monkeypatch.setattr(
        distribution,
        "fire_job",
        lambda job_id, channel, payload: fired.append((job_id, channel, payload)),
    )
    body = distribution.SendRequest(
        libraryItemId="library-item",
        channels=["bluesky"],
        text="hello",
        imageUrl="/outputs/social/run/post-image.png",
    )

    distribution.send(body)

    history = json.loads(stored[0]["payload"])
    assert history["imageUrl"] == "/outputs/social/run/post-image.png"
    assert history["imageUrls"] == [
        "/outputs/.distribution-media/prepared.bluesky.webp"
    ]
    assert fired[0][2]["imageUrl"] == "signed:/outputs/social/run/post-image.png"
    assert fired[0][2]["imageUrls"] == [
        "signed:/outputs/.distribution-media/prepared.bluesky.webp"
    ]


def test_announced_wsl_host_is_used_directly_for_local_media(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    image = root / "social" / "run" / "post.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(share_server, "is_listening", lambda: True)
    monkeypatch.setattr(distribution, "ENGINE_HOST_BASE", "")
    monkeypatch.setattr(distribution, "_announced_share_host", "172.30.240.1")

    shared = distribution._shareable_media_url("/outputs/social/run/post.png")

    assert shared.startswith("http://172.30.240.1:8756/shared/")
    assert "host.docker.internal" not in shared


def test_missing_local_media_is_rejected_instead_of_becoming_text_only(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")

    with pytest.raises(distribution.HTTPException, match="no longer exists"):
        distribution._shareable_media_url("/outputs/mastodon/missing/post.png")


def test_legacy_signed_bluesky_job_gets_canonical_array_and_fresh_link(tmp_path, monkeypatch):
    from app.services import share_links

    root = tmp_path / "outputs"
    image = root / "social" / "run" / "post.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"small image")
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(share_links, "_SECRET_FILE", tmp_path / "share-secret")
    legacy_url = share_links.url_for(image, "http://host.docker.internal:8756")
    monkeypatch.setattr(
        distribution,
        "_shareable_media_url",
        lambda url: f"fresh:{url}",
    )

    canonical = distribution._upgrade_legacy_media_payload(
        {"text": "hello", "imageUrl": legacy_url}, "bluesky"
    )
    materialized = distribution._materialize_media_payload(canonical)

    assert canonical["imageUrl"] == "/outputs/social/run/post.png"
    assert canonical["imageUrls"] == ["/outputs/social/run/post.png"]
    assert materialized["imageUrls"] == ["fresh:/outputs/social/run/post.png"]


def test_due_media_waits_when_listener_is_still_starting(monkeypatch):
    job = {"id": "scheduled-1", "channel": "bluesky", "payload": '{"imageUrl":"/outputs/post.png"}'}
    updates: list[tuple] = []
    monkeypatch.setattr(distribution.db, "list_due_scheduled_jobs", lambda: [job])
    monkeypatch.setattr(distribution.activepieces_client, "list_flows", lambda: [])
    monkeypatch.setattr(
        distribution,
        "_materialize_media_payload",
        lambda _payload: (_ for _ in ()).throw(
            distribution.HTTPException(status_code=503, detail="listener starting")
        ),
    )
    monkeypatch.setattr(
        distribution.db,
        "update_distribution_job",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    monkeypatch.setattr(
        distribution,
        "fire_job",
        lambda *_args: (_ for _ in ()).throw(AssertionError("job fired before media was reachable")),
    )

    distribution._fire_due_scheduled_jobs()

    assert updates == []


def test_all_due_jobs_wait_while_posting_engine_starts(monkeypatch):
    job = {"id": "scheduled-text", "channel": "bluesky", "payload": '{"text":"hello"}'}
    monkeypatch.setattr(distribution.db, "list_due_scheduled_jobs", lambda: [job])
    monkeypatch.setattr(
        distribution.activepieces_client,
        "list_flows",
        lambda: (_ for _ in ()).throw(
            activepieces_client.ActivepiecesError("GET /api/v1/flows failed to connect")
        ),
    )
    monkeypatch.setattr(
        distribution,
        "fire_job",
        lambda *_args: (_ for _ in ()).throw(AssertionError("job fired before engine startup")),
    )

    distribution._fire_due_scheduled_jobs()


def test_due_native_mastodon_media_waits_for_launch_credential(monkeypatch):
    job = {
        "id": "scheduled-media",
        "channel": "mastodon",
        "payload": '{"text":"hello","imageUrl":"/outputs/post.png"}',
    }
    mastodon_delivery.set_credentials()
    monkeypatch.setattr(distribution.db, "list_due_scheduled_jobs", lambda: [job])
    monkeypatch.setattr(
        distribution.db,
        "claim_scheduled_distribution_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("job was claimed before its credential arrived")
        ),
    )

    distribution._fire_due_scheduled_jobs()


def test_due_native_mastodon_media_does_not_need_posting_engine(monkeypatch):
    job = {
        "id": "scheduled-media",
        "channel": "mastodon",
        "payload": '{"text":"hello","imageUrl":"/outputs/post.png"}',
    }
    fired: list[tuple] = []
    mastodon_delivery.set_credentials("example.social", "token")
    monkeypatch.setattr(distribution.db, "list_due_scheduled_jobs", lambda: [job])
    monkeypatch.setattr(
        distribution.activepieces_client,
        "list_flows",
        lambda: (_ for _ in ()).throw(
            AssertionError("native Mastodon media preflighted Activepieces")
        ),
    )
    monkeypatch.setattr(
        distribution,
        "_materialize_media_payload",
        lambda _payload: (_ for _ in ()).throw(
            AssertionError("native Mastodon media was turned into a share URL")
        ),
    )
    monkeypatch.setattr(
        distribution.db,
        "claim_scheduled_distribution_job",
        lambda *_args, **_kwargs: job,
    )
    monkeypatch.setattr(
        distribution,
        "fire_job",
        lambda *args: fired.append(args),
    )
    try:
        distribution._fire_due_scheduled_jobs()
    finally:
        mastodon_delivery.set_credentials()

    assert fired == [
        (
            "scheduled-media",
            "mastodon",
            {
                "text": "hello",
                "imageUrl": "/outputs/post.png",
                "mediaUrl": "/outputs/post.png",
            },
        )
    ]


def test_local_video_is_validated_and_materialized_for_bluesky_and_mastodon(
    tmp_path, monkeypatch
):
    root = tmp_path / "outputs"
    video = root / "uploads" / "run" / "my clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"small pretend mp4")
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(
        distribution,
        "_shareable_media_url",
        lambda url, _scheduled=None: f"shared:{url}",
    )
    body = distribution.SendRequest(
        libraryItemId="library-item",
        channels=["bluesky", "mastodon"],
        text="hello",
        videoFileUrl="/outputs/uploads/run/my%20clip.mp4",
        videoFileAlt="A short demo",
    )

    canonical = distribution._payload_for(body)
    payload = distribution._materialize_media_payload(canonical)

    assert canonical["videoUrl"] == "/outputs/uploads/run/my%20clip.mp4"
    assert payload["videoUrl"] == "shared:/outputs/uploads/run/my%20clip.mp4"
    assert payload["mediaUrl"] == payload["videoUrl"]
    assert payload["videoFileAlt"] == "A short demo"
    assert "imageUrl" not in payload
    assert payload["imageUrls"] == []


def test_run_correlation_uses_delivery_marker_instead_of_newest_run(monkeypatch):
    delivered: list[dict] = []
    updates: list[tuple[str, dict]] = []
    runs = {
        "run-other": {
            "id": "run-other",
            "status": "SUCCEEDED",
            "steps": {
                "trigger": {
                    "output": {
                        "body": {distribution._DELIVERY_JOB_FIELD: "other-job"}
                    }
                }
            },
        },
        "run-ours": {
            "id": "run-ours",
            "status": "SUCCEEDED",
            "steps": {
                "trigger": {
                    "output": {
                        "body": {distribution._DELIVERY_JOB_FIELD: "job-1"}
                    }
                }
            },
        },
    }
    monkeypatch.setattr(
        distribution.activepieces_client,
        "trigger_webhook",
        lambda _channel, payload: delivered.append(payload),
    )
    monkeypatch.setattr(
        distribution.activepieces_client, "get_flow_id", lambda _channel: "flow"
    )
    monkeypatch.setattr(
        distribution.activepieces_client,
        "list_flow_runs_since",
        lambda *_args: [
            {"id": "run-other", "status": "SUCCEEDED"},
            {"id": "run-ours", "status": "SUCCEEDED"},
        ],
    )
    monkeypatch.setattr(
        distribution.activepieces_client,
        "get_flow_run",
        lambda run_id: runs[run_id],
    )
    monkeypatch.setattr(distribution.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        distribution.db,
        "update_distribution_job",
        lambda job_id, **fields: updates.append((job_id, fields)),
    )

    distribution.fire_job(
        "job-1", "bluesky", {"text": "hello", "imageUrls": []}
    )

    assert delivered[0][distribution._DELIVERY_JOB_FIELD] == "job-1"
    assert updates == [
        ("job-1", {"status": "sent", "activepieces_run_id": "run-ours"})
    ]


def test_native_mastodon_fire_job_bypasses_activepieces(monkeypatch):
    updates: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        mastodon_delivery,
        "publish",
        lambda _payload, **_kwargs: {
            "id": "status-1",
            "media_attachments": [{"id": "media-1", "type": "image"}],
        },
    )
    monkeypatch.setattr(
        distribution.activepieces_client,
        "trigger_webhook",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("native Mastodon media reached Activepieces")
        ),
    )
    monkeypatch.setattr(
        distribution.db,
        "update_distribution_job",
        lambda job_id, **fields: updates.append((job_id, fields)),
    )

    distribution.fire_job(
        "job-1",
        "mastodon",
        {"text": "hello", "imageUrl": "/outputs/post.png"},
    )

    assert updates == [
        (
            "job-1",
            {"status": "sent", "activepieces_run_id": "mastodon:status-1"},
        )
    ]


def test_native_mastodon_image_upload_is_attached(monkeypatch):
    uploaded: list[tuple] = []
    posted: list[tuple] = []
    mastodon_delivery.set_credentials("https://example.social", "token")
    monkeypatch.setattr(
        mastodon_delivery.image_prompt,
        "attachment_bytes",
        lambda _url: ("post.png", b"image"),
    )
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "upload_media",
        lambda *args, **kwargs: uploaded.append((args, kwargs)) or "media-1",
    )
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "api_post",
        lambda *args, **kwargs: posted.append((args, kwargs))
        or {
            "id": "status-1",
            "media_attachments": [{"id": "media-1", "type": "image"}],
        },
    )
    try:
        created = mastodon_delivery.publish(
            {"text": "hello", "imageUrl": "/outputs/post.png"},
            idempotency_key="job-1",
        )
    finally:
        mastodon_delivery.set_credentials()

    assert created["media_attachments"][0]["type"] == "image"
    assert uploaded[0][0][0:4] == (
        "example.social",
        "token",
        "post.png",
        b"image",
    )
    assert posted[0][0][3]["media_ids"] == ["media-1"]
    assert posted[0][1]["idempotency_key"] == "job-1"


def test_native_mastodon_video_upload_is_attached_with_alt_text(monkeypatch):
    uploaded: list[tuple] = []
    mastodon_delivery.set_credentials("example.social", "token")
    monkeypatch.setattr(
        mastodon_delivery.video_attach,
        "attachment_bytes",
        lambda *_args: ("clip.mp4", b"video"),
    )
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "upload_media",
        lambda *args, **kwargs: uploaded.append((args, kwargs)) or "media-video",
    )
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "api_post",
        lambda *_args, **_kwargs: {
            "id": "status-video",
            "media_attachments": [{"id": "media-video", "type": "video"}],
        },
    )
    try:
        created = mastodon_delivery.publish(
            {
                "text": "hello",
                "videoUrl": "/outputs/clip.mp4",
                "videoFileAlt": "A short demo",
            },
            idempotency_key="job-video",
        )
    finally:
        mastodon_delivery.set_credentials()

    assert created["media_attachments"][0]["type"] == "video"
    assert uploaded[0][1]["description"] == "A short demo"


def test_native_mastodon_text_only_result_is_deleted_and_failed(monkeypatch):
    deleted: list[tuple] = []
    mastodon_delivery.set_credentials("example.social", "token")
    monkeypatch.setattr(
        mastodon_delivery.image_prompt,
        "attachment_bytes",
        lambda _url: ("post.png", b"image"),
    )
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "upload_media",
        lambda *_args, **_kwargs: "media-1",
    )
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "api_post",
        lambda *_args, **_kwargs: {
            "id": "status-empty",
            "media_attachments": [],
        },
    )
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "api_delete",
        lambda *args: deleted.append(args),
    )
    try:
        with pytest.raises(
            mastodon_delivery.mastodon.MastodonError,
            match="without its attachment",
        ):
            mastodon_delivery.publish(
                {"text": "hello", "imageUrl": "/outputs/post.png"},
                idempotency_key="job-1",
            )
    finally:
        mastodon_delivery.set_credentials()

    assert deleted == [
        ("example.social", "/api/v1/statuses/status-empty", "token")
    ]


def test_distribution_rejects_image_and_video_together():
    body = distribution.SendRequest(
        libraryItemId="library-item",
        channels=["bluesky"],
        text="hello",
        imageUrl="https://images.example/post.png",
        videoFileUrl="/outputs/uploads/run/clip.mp4",
    )

    with pytest.raises(distribution.HTTPException, match="either an image or a video"):
        distribution._payload_for(body)


def test_oversize_local_image_gets_cached_bluesky_safe_copy(tmp_path, monkeypatch):
    from PIL import Image

    root = tmp_path / "outputs"
    source = root / "social" / "run" / "post-image.png"
    source.parent.mkdir(parents=True)
    pixels = random.Random(7).randbytes(900 * 900 * 3)
    Image.frombytes("RGB", (900, 900), pixels).save(source, format="PNG")
    assert source.stat().st_size > image_prompt.BLUESKY_MAX_IMAGE_BYTES
    original = source.read_bytes()

    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    prepared_url = image_prompt.prepare_bluesky_image(
        "/outputs/social/run/post-image.png"
    )
    prepared = root / prepared_url.removeprefix("/outputs/")

    assert prepared.suffix == ".webp"
    assert prepared.stat().st_size <= image_prompt.BLUESKY_TARGET_IMAGE_BYTES
    assert source.read_bytes() == original
    with Image.open(prepared) as converted:
        assert converted.size == (900, 900)
        assert not converted.getexif()

    modified = prepared.stat().st_mtime_ns
    assert (
        image_prompt.prepare_bluesky_image("/outputs/social/run/post-image.png")
        == prepared_url
    )
    assert prepared.stat().st_mtime_ns == modified


def test_unreadable_oversize_local_image_fails_before_queueing(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    source = root / "social" / "run" / "broken.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not an image" * 100_001)
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)

    with pytest.raises(image_prompt.ImageRenderError, match="not a readable image"):
        image_prompt.prepare_bluesky_image("/outputs/social/run/broken.png")
