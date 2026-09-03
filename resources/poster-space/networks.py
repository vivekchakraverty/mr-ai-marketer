"""Posting to Mastodon and Bluesky.

Deliberately a small, dependency-light port of what the desktop app already does, not an
import of it — this runs in a Space with four packages installed, and the app's transport
modules pull in the whole backend.

Two things carried over on purpose, because both were learned the hard way:

  * The upload timeout is sized from the payload. A socket timeout is not a deadline for the
    call, it is the deadline for each socket operation, and writing a 29MB body is one of
    them. A flat 20 seconds needs a sustained 12Mbit/s uplink, and when it does not get one
    the failure arrives as SSLWantWriteError rather than as a timeout.
  * Mastodon's v2 media endpoint answers 202 while it transcodes video, and a status that
    attaches an id before processing finishes is refused with "Cannot attach files that have
    not finished processing."

Idempotency is not decoration here. This Space and the desktop app can both believe a job is
due (they should not — see the scheduled_cloud handoff — but a bug in that handoff must not
cost a double post), and a lost response looks exactly like a failure from this side.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "MrAIMarketer-Poster/1.0"

#: Enough for connect, TLS handshake and a small body on a poor line.
UPLOAD_BASE_SECONDS = 60
#: A ceiling, so a stalled transfer cannot hold a tick open forever.
UPLOAD_MAX_SECONDS = 900
#: ~1Mbit/s. Pessimistic on purpose: this is the speed below which waiting is abandoned,
#: not the speed anyone expects. Assuming a good uplink is exactly what broke before.
UPLOAD_FLOOR_BYTES_PER_SECOND = 128 * 1024

MEDIA_PROCESSING_TIMEOUT = 120
MEDIA_POLL_SECONDS = 3


class PostError(RuntimeError):
    """Reported back to the app in the outcome file; written to be read by a person."""


def upload_timeout(size_bytes: int) -> float:
    return min(
        UPLOAD_BASE_SECONDS + size_bytes / UPLOAD_FLOOR_BYTES_PER_SECOND,
        float(UPLOAD_MAX_SECONDS),
    )


def _mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


# ---------------------------------------------------------------------------
# Mastodon
# ---------------------------------------------------------------------------


def post_mastodon(
    host: str,
    token: str,
    text: str,
    media: tuple[str, bytes] | None,
    alt: str,
    idempotency_key: str,
) -> str:
    """Publish one status and return its id."""
    if not host or not token:
        raise PostError("Mastodon is not connected for cloud posting.")

    headers = {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}
    media_ids: list[str] = []

    if media is not None:
        filename, content = media
        budget = upload_timeout(len(content))
        try:
            with httpx.Client(timeout=budget) as client:
                resp = client.post(
                    f"https://{host}/api/v2/media",
                    headers=headers,
                    files={"file": (filename, content, _mime(filename))},
                    data={"description": alt[:1500]} if alt.strip() else None,
                )
        except httpx.HTTPError as err:
            raise PostError(
                f"Could not reach {host} to upload {filename} "
                f"({len(content) / (1024 * 1024):.1f}MB, gave up after {budget:.0f}s): {err}"
            ) from None
        if resp.status_code not in (200, 202):
            raise PostError(f"{host} refused the attachment: {_detail(resp)}")
        media_id = str((resp.json() or {}).get("id") or "")
        if not media_id:
            raise PostError(f"{host} accepted the attachment but returned no id for it.")
        if resp.status_code == 202:
            _wait_for_media(host, headers, media_id)
        media_ids.append(media_id)

    body: dict[str, Any] = {"status": text}
    if media_ids:
        body["media_ids"] = media_ids
    try:
        with httpx.Client(timeout=60) as client:
            created = client.post(
                f"https://{host}/api/v1/statuses",
                headers={**headers, "Idempotency-Key": idempotency_key},
                json=body,
            )
    except httpx.HTTPError as err:
        raise PostError(f"Could not reach {host} to publish: {err}") from None
    if created.status_code >= 400:
        raise PostError(f"{host} refused the post: {_detail(created)}")

    payload = created.json() or {}
    # The same refusal the app's own delivery path makes: never let a connector anomaly turn
    # into an apparently-successful text-only post when media was meant to be attached.
    if media_ids and not payload.get("media_attachments"):
        raise PostError(f"{host} created the status without its attachment.")
    return str(payload.get("id") or "")


def _wait_for_media(host: str, headers: dict[str, str], media_id: str) -> None:
    """Block until the server has finished transcoding. 206 means still working, 200 ready."""
    import time

    deadline = time.monotonic() + MEDIA_PROCESSING_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"https://{host}/api/v1/media/{media_id}", headers=headers)
        except httpx.HTTPError:
            time.sleep(MEDIA_POLL_SECONDS)
            continue
        if resp.status_code == 200:
            return
        if resp.status_code not in (206, 404):
            raise PostError(f"{host} refused the attachment: {_detail(resp)}")
        time.sleep(MEDIA_POLL_SECONDS)
    raise PostError(
        f"{host} is still processing that video after {MEDIA_PROCESSING_TIMEOUT} seconds."
    )


def _detail(resp: httpx.Response) -> str:
    """What went wrong, in the service's own words.

    `error` is a bare code — "InvalidRequest" tells nobody anything. `message` is the sentence
    that names the actual problem, and dropping it turned a solvable failure into a guess.
    Both, when both exist.
    """
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        code = str(body.get("error") or body.get("error_description") or "").strip()
        message = str(body.get("message") or "").strip()
        if code and message and message != code:
            return f"{code}: {message}"
        if code or message:
            return code or message
    text = (resp.text or "").strip()
    return f"HTTP {resp.status_code}" + (f": {text[:300]}" if text else "")


# ---------------------------------------------------------------------------
# Bluesky
# ---------------------------------------------------------------------------


#: Bluesky does NOT accept a video as an ordinary blob. app.bsky.embed.video needs a blob
#: produced by a separate video service, which transcodes and only then hands one back — so a
#: plain com.atproto.repo.uploadBlob of an MP4 uploads fine and is then refused at putRecord
#: with a bare "InvalidRequest". Env-overridable because it is Bluesky's infrastructure rather
#: than the user's, and a move should not need a release.
VIDEO_SERVICE = os.environ.get("BLUESKY_VIDEO_SERVICE", "https://video.bsky.app").rstrip("/")

#: Transcoding is the slow part and happens after the bytes are in.
VIDEO_JOB_TIMEOUT = 900
VIDEO_POLL_SECONDS = 5

#: A record key for a post must be a TID, not any old unique string. Bluesky says so:
#:
#:     Invalid record key for app.bsky.feed.post:
#:     Invalid TID string (got "c296d17caec24cc78a54a65334cf79cd")
#:
#: 13 characters of base32-sortable, and because a TID's top bit is always zero the first
#: character is restricted to the low half of the alphabet.
_TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"


def tid_for(job_id: str) -> str:
    """A valid, STABLE record key for one job.

    Derived from the job id rather than the clock, and that is the whole point: putRecord at a
    fixed rkey overwrites its own record, so a retry of the same job lands on the same post
    instead of making a second one. A time-based TID would be different on every attempt and
    would turn one retry into two posts — which is exactly the failure the key exists to
    prevent.

    Ordering is the trade. TIDs are normally chronological and these are not; clients sort by
    the record's own createdAt, so what is lost is repo ordering rather than anything a reader
    sees.
    """
    import hashlib

    value = int.from_bytes(hashlib.sha256(job_id.encode("utf-8")).digest()[:8], "big")
    value &= (1 << 63) - 1  # top bit zero, which is what constrains the leading character
    return "".join(_TID_ALPHABET[(value >> (5 * i)) & 31] for i in range(12, -1, -1))


#: The lexicon the upload token must be bound to, and it is NOT the method being called.
#: Bluesky says so itself, verbatim:
#:
#:     invalid token lexicon method "app.bsky.video.uploadVideo",
#:     should be com.atproto.repo.uploadBlob
#:
#: Which follows once you read what the video service IS: something uploading a blob on the
#: account's behalf, so the token it presents is a blob-upload token. Guessing the method you
#: are calling is the natural mistake and the wrong one.
UPLOAD_LXM = "com.atproto.repo.uploadBlob"


def _pds_did(pds: str, did: str, session: dict[str, Any] | None = None) -> str:
    """The `did:web:` identifier of the account's OWN PDS.

    This is the audience the video service demands, and it is NOT the video service's own DID
    — asking for that gets back, verbatim:

        invalid token audience "did:web:video.bsky.app", should be the user's PDS DID
        "did:web:stropharia.us-west.host.bsky.network"

    Nor is it derivable from the host we talk to: everyone connects through the bsky.social
    entryway, while each account lives on a specific host behind it. So the account's DID
    document has to be read. createSession/refreshSession usually inline it as `didDoc`, which
    costs nothing; otherwise resolve it — plc.directory for did:plc, the domain itself for
    did:web.
    """
    doc = (session or {}).get("didDoc")
    if not isinstance(doc, dict):
        try:
            if did.startswith("did:plc:"):
                url = f"https://plc.directory/{did}"
            elif did.startswith("did:web:"):
                url = f"https://{did[len('did:web:'):]}/.well-known/did.json"
            else:
                raise PostError(f"Cannot resolve an unfamiliar DID method: {did}")
            with httpx.Client(timeout=30) as client:
                resp = client.get(url, headers={"User-Agent": USER_AGENT})
            doc = resp.json() if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            doc = {}

    endpoint = ""
    for service in (doc or {}).get("service") or []:
        if str(service.get("id") or "").endswith("#atproto_pds"):
            endpoint = str(service.get("serviceEndpoint") or "")
            break
    # Falling back to the host we are already talking to is wrong often enough to be worth
    # saying so rather than failing with the service's own opaque refusal later.
    host = endpoint.replace("https://", "").replace("http://", "").strip("/")
    if not host:
        raise PostError(
            "Could not work out which Bluesky server this account lives on, so the video "
            "upload cannot be authorised."
        )
    return f"did:web:{host}"


def _service_auth(pds: str, access_jwt: str, aud: str, lxm: str) -> str:
    """A short-lived token scoped to ONE method on ONE service.

    The video service is not the PDS and will not take the session token. getServiceAuth mints
    a token the PDS signs and the video service trusts, bound by `lxm` to the single method it
    is for — so the token handed to the uploader cannot be replayed against anything else.
    """
    try:
        with httpx.Client(timeout=45) as client:
            resp = client.get(
                f"{pds}/xrpc/com.atproto.server.getServiceAuth",
                params={"aud": aud, "lxm": lxm},
                headers={"Authorization": f"Bearer {access_jwt}", "User-Agent": USER_AGENT},
            )
    except httpx.HTTPError as err:
        raise PostError(f"Could not reach {pds} for video permission: {err}") from None
    if resp.status_code >= 400:
        raise PostError(f"Bluesky would not authorise a video upload: {_detail(resp)}")
    token = str((resp.json() or {}).get("token") or "")
    if not token:
        raise PostError("Bluesky returned no token for the video upload.")
    return token


def _video_limits(pds: str, access_jwt: str, aud: str) -> str:
    """'' when a video may be uploaded, otherwise why not.

    Checked first because the daily allowance is a real limit people hit, and 'you have used
    today's video allowance' is worth far more than the generic refusal that comes back
    otherwise. Never fatal by itself: if the check cannot be made, the upload still tries.
    """
    try:
        token = _service_auth(pds, access_jwt, aud, "app.bsky.video.getUploadLimits")
        with httpx.Client(timeout=45) as client:
            resp = client.get(
                f"{VIDEO_SERVICE}/xrpc/app.bsky.video.getUploadLimits",
                headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            )
        if resp.status_code >= 400:
            return ""
        body = resp.json() or {}
    except (httpx.HTTPError, PostError, ValueError):
        return ""
    if body.get("canUpload") is False:
        return str(body.get("message") or body.get("error") or "Bluesky will not accept a video right now.")
    return ""


def upload_bluesky_video(
    pds: str,
    did: str,
    access_jwt: str,
    filename: str,
    content: bytes,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Put a video through Bluesky's video service and return the blob to embed."""
    aud = _pds_did(pds, did, session)

    blocked = _video_limits(pds, access_jwt, aud)
    if blocked:
        raise PostError(f"Bluesky refused the video: {blocked}")

    token = _service_auth(pds, access_jwt, aud, UPLOAD_LXM)
    budget = upload_timeout(len(content))
    try:
        with httpx.Client(timeout=budget) as client:
            resp = client.post(
                f"{VIDEO_SERVICE}/xrpc/app.bsky.video.uploadVideo",
                params={"did": did, "name": filename},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": _mime(filename),
                    "User-Agent": USER_AGENT,
                },
                content=content,
            )
    except httpx.HTTPError as err:
        raise PostError(
            f"Could not upload {filename} to Bluesky's video service "
            f"({len(content) / (1024 * 1024):.1f}MB, gave up after {budget:.0f}s): {err}"
        ) from None

    body: dict[str, Any] = {}
    try:
        parsed = resp.json()
        body = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        pass

    # The lexicon says the response is {"jobStatus": {...}}, and the service has also been
    # observed answering with the job status unwrapped. Accept either rather than depending on
    # which one is true today — the shape is somebody else's to change.
    nested = body.get("jobStatus")
    job: dict[str, Any] = nested if isinstance(nested, dict) else body

    # Re-uploading identical bytes is answered with the existing job rather than a new one.
    # That is a success for our purposes: the same video is already being processed.
    if resp.status_code >= 400 and not job.get("jobId"):
        raise PostError(f"Bluesky's video service refused {filename}: {_detail(resp)}")

    if job.get("blob"):
        return job["blob"]
    job_id = str(job.get("jobId") or "")
    if not job_id:
        # Quote what actually came back. An unreadable SUCCESS is the one case where the
        # response body is the only thing that can say what to do next, and guessing at it
        # from outside costs a scheduled post per guess.
        raise PostError(
            f"Bluesky's video service accepted {filename} but its reply had no job to follow "
            f"(HTTP {resp.status_code}, body: {str(body)[:400] or resp.text[:400]!r})"
        )
    return _await_video_job(pds, access_jwt, aud, job_id, filename)


def _await_video_job(
    pds: str, access_jwt: str, aud: str, job_id: str, filename: str
) -> dict[str, Any]:
    """Poll until the video is transcoded, and return its blob."""
    import time

    deadline = time.monotonic() + VIDEO_JOB_TIMEOUT
    # Which credential the status endpoint wants is the one thing here Bluesky has not told us
    # outright, so try the plausible shapes in order rather than looping on a single theory.
    # `None` means unauthenticated, which is how the public job-status endpoint behaves.
    lxm_options: list[str | None] = [UPLOAD_LXM, "app.bsky.video.getJobStatus", None]
    option = 0
    token = ""
    while time.monotonic() < deadline:
        try:
            lxm = lxm_options[min(option, len(lxm_options) - 1)]
            if lxm is not None and not token:
                token = _service_auth(pds, access_jwt, aud, lxm)
            headers = {"User-Agent": USER_AGENT}
            if lxm is not None:
                headers["Authorization"] = f"Bearer {token}"
            with httpx.Client(timeout=45) as client:
                resp = client.get(
                    f"{VIDEO_SERVICE}/xrpc/app.bsky.video.getJobStatus",
                    params={"jobId": job_id},
                    headers=headers,
                )
        except (httpx.HTTPError, PostError):
            time.sleep(VIDEO_POLL_SECONDS)
            continue
        if resp.status_code == 401:
            # Either the token expired under a long transcode, or this shape is wrong. Re-mint
            # first; if that does not help, move on to the next shape.
            if token:
                token = ""
            else:
                option += 1
            time.sleep(VIDEO_POLL_SECONDS)
            continue
        if resp.status_code >= 400:
            raise PostError(f"Bluesky could not report on the video: {_detail(resp)}")

        job = (resp.json() or {}).get("jobStatus") or {}
        state = str(job.get("state") or "")
        if state == "JOB_STATE_COMPLETED":
            blob = job.get("blob")
            if not blob:
                raise PostError("Bluesky finished processing the video but returned no blob.")
            return blob
        if state == "JOB_STATE_FAILED":
            raise PostError(
                f"Bluesky could not process {filename}: {job.get('error') or job.get('message') or state}"
            )
        time.sleep(VIDEO_POLL_SECONDS)

    raise PostError(
        f"Bluesky is still processing {filename} after {VIDEO_JOB_TIMEOUT} seconds."
    )


def refresh_bluesky(pds: str, refresh_jwt: str) -> dict[str, Any]:
    """Exchange a refresh token for an access token, and for a new refresh token.

    refreshSession ROTATES: the response carries a fresh refreshJwt and the one just used
    stops working. The caller must persist the new one or the next tick cannot authenticate.
    """
    try:
        with httpx.Client(timeout=45) as client:
            resp = client.post(
                f"{pds}/xrpc/com.atproto.server.refreshSession",
                headers={"Authorization": f"Bearer {refresh_jwt}", "User-Agent": USER_AGENT},
            )
    except httpx.HTTPError as err:
        raise PostError(f"Could not reach {pds}: {err}") from None
    if resp.status_code >= 400:
        raise PostError(f"Bluesky would not renew the session: {_detail(resp)}")
    return resp.json() or {}


def post_bluesky(
    pds: str,
    did: str,
    access_jwt: str,
    text: str,
    media: tuple[str, bytes] | None,
    alt: str,
    aspect_ratio: dict[str, int] | None,
    rkey: str,
    session: dict[str, Any] | None = None,
) -> str:
    """Create one post at a known record key and return its at:// URI.

    putRecord with swapRecord: null rather than createRecord, because the record key is then
    ours to choose and the write is create-only-if-absent. A repeat of the same job lands on
    the same rkey and is refused by the server rather than becoming a second post — which is
    a guarantee the Activepieces path cannot make.
    """
    if not did or not access_jwt:
        raise PostError("Bluesky is not connected for cloud posting.")

    headers = {"Authorization": f"Bearer {access_jwt}", "User-Agent": USER_AGENT}
    record: dict[str, Any] = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": _now_iso(),
    }

    if media is not None:
        filename, content = media
        # Two genuinely different routes, which is the whole point of this branch. A video has
        # to go through Bluesky's video service and be transcoded before it yields a blob the
        # embed will accept; an image is an ordinary blob on the PDS. Uploading a video the
        # image way succeeds and is then refused at putRecord with a bare "InvalidRequest".
        if _mime(filename).startswith("video/"):
            ref = upload_bluesky_video(pds, did, access_jwt, filename, content, session)
            embed: dict[str, Any] = {"$type": "app.bsky.embed.video", "video": ref}
            if alt.strip():
                embed["alt"] = alt.strip()[:1000]
            if aspect_ratio:
                embed["aspectRatio"] = aspect_ratio
        else:
            budget = upload_timeout(len(content))
            try:
                with httpx.Client(timeout=budget) as client:
                    blob = client.post(
                        f"{pds}/xrpc/com.atproto.repo.uploadBlob",
                        headers={**headers, "Content-Type": _mime(filename)},
                        content=content,
                    )
            except httpx.HTTPError as err:
                raise PostError(
                    f"Could not upload {filename} to Bluesky "
                    f"({len(content) / (1024 * 1024):.1f}MB, gave up after {budget:.0f}s): {err}"
                ) from None
            if blob.status_code >= 400:
                raise PostError(f"Bluesky refused the attachment: {_detail(blob)}")
            ref = (blob.json() or {}).get("blob")
            if not ref:
                raise PostError("Bluesky accepted the attachment but returned no reference.")
            image: dict[str, Any] = {"image": ref, "alt": alt.strip()[:1000]}
            if aspect_ratio:
                image["aspectRatio"] = aspect_ratio
            embed = {"$type": "app.bsky.embed.images", "images": [image]}
        record["embed"] = embed

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{pds}/xrpc/com.atproto.repo.putRecord",
                headers=headers,
                json={
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "rkey": rkey,
                    "record": record,
                    # No swapRecord. It was here to mean create-only-if-absent, but the record
                    # key is already derived from the job id — so a repeat writes the SAME
                    # record rather than a second post, which is the guarantee that was
                    # actually wanted. Sending an explicit null bought nothing beyond a
                    # distinct error on a repeat, and is a live suspect for the InvalidRequest
                    # this call was returning.
                },
            )
    except httpx.HTTPError as err:
        raise PostError(f"Could not reach {pds} to publish: {err}") from None

    if resp.status_code >= 400:
        detail = _detail(resp)
        if "InvalidSwap" in detail or "swap" in detail.lower():
            # The record is already there, so this job has already been posted. Success.
            return f"at://{did}/app.bsky.feed.post/{rkey}"
        raise PostError(f"Bluesky refused the post: {detail}")
    return str((resp.json() or {}).get("uri") or f"at://{did}/app.bsky.feed.post/{rkey}")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
