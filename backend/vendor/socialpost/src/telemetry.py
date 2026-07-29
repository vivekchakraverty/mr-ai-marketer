"""Pooled telemetry client — the send side of the ingest Space.

The premise the user chose: participation is a precondition of use, exposure is
not. So this is TWO tiers (mirrored exactly by the Space's schema):

  Mandatory  Derived metrics only. Config fingerprint, exemplar/KB *references*
             (public URIs and numbers, never text), the 48h outcome, and the
             draft-vs-published edit distance computed locally as a single float.
             Nothing here is anyone's post text or prompt text.

  Opt-in     The raw user_input and generated output_text. Off by default; a user
             turns it on explicitly and can turn it back off.

Inert by default
----------------
Everything here is a no-op unless TELEMETRY_ENDPOINT is set. A fresh clone, a
private user who does not want to participate, or the master-app integration
without pooling: none of them see a consent gate or send anything. Setting the
endpoint (which the pooled distribution ships with) is what activates the gate.

Never blocks generation
-----------------------
Records are queued in telemetry_outbox and drained best-effort by the telemetry
job. A sleeping Space, a network blip, or a 500 delays delivery; it never delays
or fails a user's post. Delivery is at-least-once — the Space dedupes on
generation_uid + kind if a delivered row's ack was lost.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import requests

from . import llm
from .db import get_client, iso, utcnow

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# Bump when the CONSENT TERMS change (what data, where it goes). A bump re-prompts
# the user — silent scope creep is how trust dies. Distinct from llm.PROMPT_VERSION,
# which fingerprints prompt wording, not consent.
CONSENT_VERSION = 1

SEND_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 8  # after this many failed sends a record is abandoned, not retried forever
BATCH_SIZE = 50

# How far back collect_outcomes scans for unreported generations. Comfortably past
# the 48h outcome window plus snapshot drift; anything older is settled either way.
OUTCOME_SCAN_DAYS = 14


class ConsentRequired(Exception):
    """Raised by generate() when telemetry is on but the user has not accepted.

    Callers catch this to show the consent flow — it is a signal, not a failure.
    """


def endpoint() -> str | None:
    """The ingest Space base URL, or None when telemetry is switched off."""
    url = (os.environ.get("TELEMETRY_ENDPOINT") or "").strip().rstrip("/")
    return url or None


def is_enabled() -> bool:
    """True when a pooling endpoint is configured. Everything gates on this."""
    return endpoint() is not None


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Consent:
    version: int
    content_opt_in: bool
    accepted_at: str


def current_consent() -> Consent | None:
    """The user's most recent consent, or None if they have never accepted.

    Append-only history: the latest row wins, older rows are the audit trail.
    """
    rows = (
        get_client()
        .table("telemetry_consent")
        .select("consent_version, content_opt_in, accepted_at")
        .order("accepted_at", desc=True)
        # id tiebreak: two acceptances in the same microsecond share accepted_at,
        # and the later insert (higher id) is the one that should win.
        .order("id", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    r = rows[0]
    return Consent(
        version=int(r["consent_version"]),
        content_opt_in=bool(r["content_opt_in"]),
        accepted_at=r["accepted_at"],
    )


def needs_consent() -> bool:
    """True when telemetry is on but the user has not accepted the current terms.

    A stale consent_version (terms changed since they accepted) counts as needing
    consent again — that is the point of versioning it.
    """
    if not is_enabled():
        return False
    consent = current_consent()
    return consent is None or consent.version < CONSENT_VERSION


def record_consent(content_opt_in: bool, identity: str | None = None) -> None:
    """Store an acceptance of the current terms. Append, never overwrite."""
    get_client().table("telemetry_consent").insert(
        {
            "consent_version": CONSENT_VERSION,
            "content_opt_in": content_opt_in,
            "identity": identity,
            "accepted_at": iso(utcnow()),
        }
    ).execute()
    log.info(
        "Telemetry consent recorded (v%d, content_opt_in=%s)",
        CONSENT_VERSION,
        content_opt_in,
    )


def content_shared() -> bool:
    """Whether the opt-in content tier is currently active."""
    consent = current_consent()
    return bool(consent and consent.content_opt_in and consent.version >= CONSENT_VERSION)


# ---------------------------------------------------------------------------
# Metrics that are derived, not raw
# ---------------------------------------------------------------------------


def edit_distance_ratio(a: str, b: str) -> float:
    """Levenshtein(a, b) / max(len) in [0, 1]. 0 = identical, 1 = nothing shared.

    Computed locally so the *number* can be shared without either text. It is the
    cheapest honest quality signal we have: a draft the user published verbatim
    (near 0) landed; one they rewrote heavily (near 1) did not. Both strings are
    short (<=300 graphemes), so the O(n*m) DP is trivial and needs no dependency.
    """
    a = a or ""
    b = b or ""
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            )
        prev = cur
    return prev[-1] / max(len(a), len(b))


def follower_bucket(count: int | None) -> str:
    """Coarsen a follower count so reach class is shared without identifying.

    Exact counts can re-identify a small account; the bucket tells us whether a
    post came from a nano/micro/large account, which is all the analysis needs.
    """
    n = count or 0
    if n < 100:
        return "<100"
    if n < 1_000:
        return "100-1k"
    if n < 10_000:
        return "1k-10k"
    if n < 100_000:
        return "10k-100k"
    return "100k+"


# ---------------------------------------------------------------------------
# Payload builders — the exact JSON the Space will receive
# ---------------------------------------------------------------------------


def build_generation_record(
    uid: str,
    niche: str,
    platform: str,
    created_at: str,
    user_input: str,
    output_text: str,
    exemplars: list[dict],
    kb_articles: list[dict],
    similarity_weight: float,
) -> dict:
    """A generation record. `exemplars`/`kb_articles` carry references, not text.

    similarity_weight is passed in rather than imported from generation.py — that
    would be a circular import (generation imports telemetry), and it is just a
    number the caller already holds.

    The content object is attached ONLY when the user opted into the content tier;
    otherwise it is absent entirely, not empty — so a metrics-tier dataset never
    contains a stray null where text would be.
    """
    record = {
        "record_type": "generation",
        "schema_version": SCHEMA_VERSION,
        "generation_uid": uid,
        "created_at": created_at,
        "niche": niche,
        "platform": platform,
        "model_id": llm.model_name(),
        "prompt_version": llm.PROMPT_VERSION,
        "retrieval": {
            "similarity_weight": round(float(similarity_weight), 4),
            "n_exemplars": len(exemplars),
            "n_kb": len(kb_articles),
        },
        "exemplars": [
            {
                "uri": e["uri"],
                "similarity": round(float(e["similarity"]), 4),
                "score": round(float(e["score"]), 6),
            }
            for e in exemplars
        ],
        "kb": [{"url_hash": k["url_hash"], "decay_weight": k.get("decay_weight")} for k in kb_articles],
    }
    if content_shared():
        record["content"] = {"user_input": user_input, "output_text": output_text}
    return record


def build_outcome_record(
    uid: str,
    engagement_rate_48h: float | None,
    baseline: float | None,
    follower_count: int | None,
    edit_ratio: float | None,
) -> dict:
    """An outcome record. All derived numbers; no text in either tier."""
    return {
        "record_type": "outcome",
        "schema_version": SCHEMA_VERSION,
        "generation_uid": uid,
        "engagement_rate_48h": engagement_rate_48h,
        "baseline_at_measure": baseline,
        "follower_bucket": follower_bucket(follower_count),
        "edit_distance_ratio": round(edit_ratio, 4) if edit_ratio is not None else None,
    }


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


def enqueue(kind: str, payload: dict) -> None:
    """Queue a record for best-effort delivery. Silent no-op when telemetry is off.

    Wrapped so a telemetry failure can never surface into a generation: the whole
    point is that the user's post does not depend on our dataset.
    """
    if not is_enabled():
        return
    try:
        get_client().table("telemetry_outbox").insert(
            {
                "kind": kind,
                "payload": json.dumps(payload, ensure_ascii=False),
                "created_at": iso(utcnow()),
                "attempts": 0,
            }
        ).execute()
    except Exception:  # noqa: BLE001
        log.exception("Failed to enqueue telemetry (%s); dropping it", kind)


def _mark(row_id: int, delivered: bool, attempts: int) -> None:
    patch: dict[str, Any] = {"attempts": attempts, "last_attempt_at": iso(utcnow())}
    if delivered:
        patch["delivered_at"] = iso(utcnow())
    get_client().table("telemetry_outbox").update(patch).eq("id", row_id).execute()


def _post(path: str, body: dict, token: str) -> bool:
    """POST to a Space endpoint. Returns True on 200, False on a retryable error.

    Raises PermissionError on 401/403 (the token cannot authenticate — retrying
    this run will not help).
    """
    resp = requests.post(
        f"{endpoint()}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=SEND_TIMEOUT_SECONDS,
    )
    if resp.status_code == 200:
        return True
    if resp.status_code in (401, 403):
        raise PermissionError(
            f"Telemetry endpoint rejected the token ({resp.status_code}). The HF "
            f"token needs no special scope — whoami is enough — so check it is set "
            f"and valid."
        )
    # A 422 means our schema drifted from the Space's — retrying will keep failing,
    # but the attempts guard eventually abandons the row rather than looping forever.
    log.warning("Telemetry %s returned %s; will retry later", path, resp.status_code)
    return False


def flush(limit: int = BATCH_SIZE) -> dict[str, int]:
    """Drain undelivered outbox rows to the Space. Returns a small summary.

    generation/outcome rows batch to /v1/records; delete_request rows go one at a
    time to /v1/delete-me (a different endpoint and schema). Best-effort: a
    transient failure leaves rows queued; the attempts guard abandons a row that
    has failed MAX_ATTEMPTS times rather than retrying it forever.
    """
    if not is_enabled():
        return {"skipped": 1}

    token = _sender_token()
    if not token:
        log.warning("Telemetry is on but no HF token is available to authenticate the send")
        return {"no_token": 1}

    rows = (
        get_client()
        .table("telemetry_outbox")
        .select("id, kind, payload, attempts")
        .is_("delivered_at", "null")
        .lt("attempts", MAX_ATTEMPTS)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
        .data
        or []
    )
    if not rows:
        return {"nothing_due": 1}

    record_rows = [r for r in rows if r["kind"] in ("generation", "outcome")]
    delete_rows = [r for r in rows if r["kind"] == "delete_request"]
    summary: dict[str, int] = {}

    try:
        if record_rows:
            records = [json.loads(r["payload"]) for r in record_rows]
            ok = _post("/v1/records", {"records": records}, token)
            for r in record_rows:
                _mark(r["id"], delivered=ok, attempts=int(r["attempts"]) + 1)
            summary["delivered" if ok else "deferred"] = len(record_rows)

        for r in delete_rows:
            payload = json.loads(r["payload"])
            ok = _post("/v1/delete-me", {"note": payload.get("note", "")}, token)
            _mark(r["id"], delivered=ok, attempts=int(r["attempts"]) + 1)
            summary["delete_sent" if ok else "delete_deferred"] = (
                summary.get("delete_sent" if ok else "delete_deferred", 0) + 1
            )
    except PermissionError as err:
        log.error("%s", err)
        return {"auth_failed": len(rows)}

    return summary or {"nothing_due": 1}


def _sender_token() -> str | None:
    """The HF token used to identify this instance to the Space.

    Reuses the same token the HF LLM provider uses; the Space only needs it to
    verify identity via whoami, not to bill anything. Falls back to the cached
    `hf auth login`, matching llm.hf_token()'s resolution.
    """
    try:
        return llm.hf_token()
    except Exception:  # noqa: BLE001 — llm raises LLMError when nothing is set
        return None


# ---------------------------------------------------------------------------
# Right to be forgotten, for the pool
# ---------------------------------------------------------------------------


def request_pool_deletion(note: str = "") -> None:
    """Queue a delete-me request to the pooled dataset maintainer.

    Local forget.py erases this instance's own database; this asks the pool to
    erase what this instance already contributed. Both are needed for a complete
    erasure once pooling is on.
    """
    enqueue("delete_request", {"record_type": "delete_request", "schema_version": SCHEMA_VERSION, "note": note})


def preview_payloads(content_opt_in: bool) -> dict[str, dict]:
    """Representative examples of exactly what this instance would send.

    For the consent screen: honest consent means showing the real shape, not
    describing it. `content_opt_in` toggles whether the raw-text content object
    appears, so a user sees precisely what each tier adds before choosing.
    """
    gen = {
        "record_type": "generation",
        "schema_version": SCHEMA_VERSION,
        "generation_uid": "3f2a…(random, non-identifying)",
        "created_at": iso(utcnow()),
        "niche": "indie makers",
        "platform": "bluesky",
        "model_id": llm.model_name(),
        "prompt_version": llm.PROMPT_VERSION,
        "retrieval": {"similarity_weight": 0.7, "n_exemplars": 5, "n_kb": 3},
        "exemplars": [
            {"uri": "at://…/app.bsky.feed.post/… (public link)", "similarity": 0.41, "score": 0.013}
        ],
        "kb": [{"url_hash": "sha256 of the article URL", "decay_weight": 1.0}],
    }
    if content_opt_in:
        gen["content"] = {
            "user_input": "(your prompt text — only because you opted in)",
            "output_text": "(the generated post — only because you opted in)",
        }
    outcome = {
        "record_type": "outcome",
        "schema_version": SCHEMA_VERSION,
        "generation_uid": "3f2a… (links to the generation above)",
        "engagement_rate_48h": 0.012,
        "baseline_at_measure": 0.0087,
        "follower_bucket": "1k-10k",
        "edit_distance_ratio": 0.18,
    }
    return {"generation": gen, "outcome": outcome}


# ---------------------------------------------------------------------------
# Outcome collection
# ---------------------------------------------------------------------------


def collect_outcomes() -> int:
    """Queue outcome records for published generations that reached 48h.

    Runs before flush. A generation qualifies when it has a posted_uri, that post
    has a 48h engagement snapshot, and no outcome has been reported yet
    (outcome_reported_at is null). Everything is computed from local DB state:
    the 48h rate, the niche baseline at measure time, the publisher's follower
    bucket, and the edit distance between what we generated and what the user
    actually published (both texts are already stored; only the derived number
    leaves).

    Returns the number of outcomes queued.
    """
    if not is_enabled():
        return 0

    client = get_client()

    # Only recent generations: one older than the outcome window that was going to
    # be reported already has been, and a never-published one would otherwise be
    # rescanned forever. This bounds the scan without a schema flag for "abandoned".
    # "is not null" on posted_uri is filtered in Python — the SQLite backend has no
    # negated-null filter, and the candidate set here is small.
    cutoff = iso(utcnow() - timedelta(days=OUTCOME_SCAN_DAYS))
    gens = [
        g
        for g in (
            client.table("generations")
            .select("id, uid, niche, output_text, posted_uri")
            .is_("outcome_reported_at", "null")
            .gte("created_at", cutoff)
            .execute()
            .data
            or []
        )
        if g.get("posted_uri") and g.get("uid")
    ]
    if not gens:
        return 0

    # Baselines by niche, to record what "normal" was when we measured.
    baselines = {
        b["scope_key"]: float(b["avg_engagement_rate"])
        for b in (
            client.table("performance_baselines")
            .select("scope_key, avg_engagement_rate")
            .eq("scope", "niche")
            .eq("window_label", "48h")
            .execute()
            .data
            or []
        )
        if b["avg_engagement_rate"] is not None
    }

    queued = 0
    for gen in gens:
        uri = gen["posted_uri"]
        snaps = (
            client.table("engagement_snapshots")
            .select("engagement_rate")
            .eq("post_uri", uri)
            .eq("window_label", "48h")
            .limit(1)
            .execute()
            .data
            or []
        )
        if not snaps:
            continue  # published, but not yet 48h old; leave it for a later run

        post = (
            client.table("posts")
            .select("text, author_did")
            .eq("uri", uri)
            .limit(1)
            .execute()
            .data
            or []
        )
        published_text = post[0]["text"] if post else ""
        follower_count = None
        if post and post[0].get("author_did"):
            author = (
                client.table("authors")
                .select("follower_count")
                .eq("did", post[0]["author_did"])
                .limit(1)
                .execute()
                .data
                or []
            )
            if author:
                follower_count = author[0]["follower_count"]

        edit_ratio = (
            edit_distance_ratio(gen["output_text"] or "", published_text)
            if published_text
            else None
        )

        enqueue(
            "outcome",
            build_outcome_record(
                uid=gen["uid"],
                engagement_rate_48h=float(snaps[0]["engagement_rate"])
                if snaps[0]["engagement_rate"] is not None
                else None,
                baseline=baselines.get(gen["niche"]),
                follower_count=follower_count,
                edit_ratio=edit_ratio,
            ),
        )
        client.table("generations").update(
            {"outcome_reported_at": iso(utcnow())}
        ).eq("id", gen["id"]).execute()
        queued += 1

    if queued:
        log.info("Queued %d outcome record(s)", queued)
    return queued
